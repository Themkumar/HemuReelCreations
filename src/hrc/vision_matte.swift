// Apple Vision person cut-out for HRC (macOS 12+, Apple Silicon Neural Engine).
// Reads raw RGB24 frames (W x H) on stdin. For each frame, writes a header of two little-endian
// UInt32 values (mask width, mask height), then the 8-bit mask rows (0 = background, 255 = person).
// Usage: vision_matte <W> <H> [accurate|balanced|fast]
import CoreVideo
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 3, let W = Int(args[1]), let H = Int(args[2]) else {
    FileHandle.standardError.write("usage: vision_matte W H [accurate|balanced|fast]\n".data(using: .utf8)!)
    exit(1)
}
let quality = args.count > 3 ? args[3] : "accurate"
let frameBytes = W * H * 3
let input = FileHandle.standardInput
let output = FileHandle.standardOutput

let request = VNGeneratePersonSegmentationRequest()
switch quality {
case "fast": request.qualityLevel = .fast
case "balanced": request.qualityLevel = .balanced
default: request.qualityLevel = .accurate
}
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
let handler = VNSequenceRequestHandler()  // keeps temporal state between frames: steadier edges

func readExactly(_ n: Int) -> Data? {
    var data = Data(capacity: n)
    while data.count < n {
        let chunk = input.readData(ofLength: n - data.count)
        if chunk.isEmpty { return nil }
        data.append(chunk)
    }
    return data
}

var pixelBuffer: CVPixelBuffer?
let attrs = [kCVPixelBufferIOSurfacePropertiesKey as String: [String: Any]()] as CFDictionary
CVPixelBufferCreate(nil, W, H, kCVPixelFormatType_32BGRA, attrs, &pixelBuffer)
guard let buf = pixelBuffer else { exit(2) }

while let frame = readExactly(frameBytes) {
    CVPixelBufferLockBaseAddress(buf, [])
    let dst = CVPixelBufferGetBaseAddress(buf)!.assumingMemoryBound(to: UInt8.self)
    let bpr = CVPixelBufferGetBytesPerRow(buf)
    frame.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
        let s = raw.bindMemory(to: UInt8.self)
        for y in 0..<H {
            let row = dst + y * bpr
            var i = y * W * 3
            for x in 0..<W {
                let o = x * 4
                row[o] = s[i + 2]; row[o + 1] = s[i + 1]; row[o + 2] = s[i]; row[o + 3] = 255
                i += 3
            }
        }
    }
    CVPixelBufferUnlockBaseAddress(buf, [])

    var mw: UInt32 = 0, mh: UInt32 = 0
    var maskData = Data()
    do {
        try handler.perform([request], on: buf)
        if let mask = request.results?.first?.pixelBuffer {
            CVPixelBufferLockBaseAddress(mask, .readOnly)
            let w = CVPixelBufferGetWidth(mask), h = CVPixelBufferGetHeight(mask)
            let mbpr = CVPixelBufferGetBytesPerRow(mask)
            let base = CVPixelBufferGetBaseAddress(mask)!.assumingMemoryBound(to: UInt8.self)
            maskData.reserveCapacity(w * h)
            for y in 0..<h { maskData.append(base + y * mbpr, count: w) }
            CVPixelBufferUnlockBaseAddress(mask, .readOnly)
            mw = UInt32(w); mh = UInt32(h)
        }
    } catch {
        FileHandle.standardError.write("vision error: \(error)\n".data(using: .utf8)!)
    }
    var header = Data()
    withUnsafeBytes(of: mw.littleEndian) { header.append(contentsOf: $0) }
    withUnsafeBytes(of: mh.littleEndian) { header.append(contentsOf: $0) }
    output.write(header)
    if mw > 0 { output.write(maskData) }
}
