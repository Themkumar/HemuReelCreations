// Apple Vision person cut-out for HRC (macOS 12+, Apple Silicon Neural Engine).
// Reads raw RGB24 frames (W x H) on stdin. For each frame, writes a header of two little-endian
// UInt32 values (mask width, mask height), then the 8-bit mask rows (0 = background, 255 = person).
// Usage: vision_matte <W> <H> [accurate|balanced|fast] [objects]
// With "objects" (macOS 14+), each frame also gets two more blocks, each with the same header:
//   the soft mask of every foreground object Vision finds (mic, cups, bottles, people...) at W x H,
//   then the instance label map (0 = background, 1..N = object number) at Vision's own size.
// A block with width 0 means "nothing found" (or not supported on this macOS).
import CoreVideo
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 3, let W = Int(args[1]), let H = Int(args[2]) else {
    FileHandle.standardError.write("usage: vision_matte W H [accurate|balanced|fast]\n".data(using: .utf8)!)
    exit(1)
}
let quality = args.count > 3 ? args[3] : "accurate"
let wantObjects = args.count > 4 && args[4] == "objects"
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

func writeBlock(_ w: UInt32, _ h: UInt32, _ data: Data) {
    var header = Data()
    withUnsafeBytes(of: w.littleEndian) { header.append(contentsOf: $0) }
    withUnsafeBytes(of: h.littleEndian) { header.append(contentsOf: $0) }
    output.write(header)
    if w > 0 { output.write(data) }
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
    writeBlock(mw, mh, maskData)
    if wantObjects {
        var ow: UInt32 = 0, oh: UInt32 = 0, lw: UInt32 = 0, lh: UInt32 = 0
        var soft = Data(), labels = Data()
        if #available(macOS 14.0, *) {
            let objReq = VNGenerateForegroundInstanceMaskRequest()
            let ih = VNImageRequestHandler(cvPixelBuffer: buf, options: [:])
            do {
                try ih.perform([objReq])
                if let obs = objReq.results?.first, !obs.allInstances.isEmpty {
                    let scaled = try obs.generateScaledMaskForImage(forInstances: obs.allInstances, from: ih)
                    CVPixelBufferLockBaseAddress(scaled, .readOnly)
                    let w = CVPixelBufferGetWidth(scaled), h = CVPixelBufferGetHeight(scaled)
                    let sbpr = CVPixelBufferGetBytesPerRow(scaled)
                    let fmt = CVPixelBufferGetPixelFormatType(scaled)
                    let base = CVPixelBufferGetBaseAddress(scaled)!
                    var bytes = [UInt8](repeating: 0, count: w * h)
                    for y in 0..<h {
                        let row = base + y * sbpr
                        if fmt == kCVPixelFormatType_OneComponent32Float {
                            let f = row.assumingMemoryBound(to: Float32.self)
                            for x in 0..<w {
                                let v = f[x] * 255
                                bytes[y * w + x] = v <= 0 ? 0 : (v >= 255 ? 255 : UInt8(v))
                            }
                        } else {
                            let b = row.assumingMemoryBound(to: UInt8.self)
                            for x in 0..<w { bytes[y * w + x] = b[x] }
                        }
                    }
                    soft = Data(bytes)
                    CVPixelBufferUnlockBaseAddress(scaled, .readOnly)
                    ow = UInt32(w); oh = UInt32(h)
                    let lab = obs.instanceMask
                    CVPixelBufferLockBaseAddress(lab, .readOnly)
                    let w2 = CVPixelBufferGetWidth(lab), h2 = CVPixelBufferGetHeight(lab)
                    let lbpr = CVPixelBufferGetBytesPerRow(lab)
                    let lbase = CVPixelBufferGetBaseAddress(lab)!.assumingMemoryBound(to: UInt8.self)
                    labels.reserveCapacity(w2 * h2)
                    for y in 0..<h2 { labels.append(lbase + y * lbpr, count: w2) }
                    CVPixelBufferUnlockBaseAddress(lab, .readOnly)
                    lw = UInt32(w2); lh = UInt32(h2)
                }
            } catch {
                FileHandle.standardError.write("vision objects error: \(error)\n".data(using: .utf8)!)
            }
        }
        writeBlock(ow, oh, soft)
        writeBlock(lw, lh, labels)
    }
}
