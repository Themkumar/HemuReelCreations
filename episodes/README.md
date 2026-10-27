# Episode data

One folder per episode: the approved reel plan and the filled English translations, so a reel can be
rebuilt later without picking or translating again.

To use one, copy it into the HRC folder:

```bash
cp episodes/HemuXSrinadh_Part1/reels_plan.json .
mkdir -p cache/translate && cp episodes/HemuXSrinadh_Part1/translate/*.json cache/translate/
hrc translate build            # never `prepare` here: lines whose Telugu changed would come back empty
```
