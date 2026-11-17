You are checking OCR transcriptions of comic lettering. This is transcription only: never translate.

The attached images are review sheets. Each sheet holds several crops from comic pages; the label printed
above each crop is its region id (for example p012_r03).

Below is the list of regions on these sheets, each with the OCR engine's reading. Compare every reading
with its crop and report ONLY the regions that need a change:

- `text`: exactly what is printed, in the source language. Join the lines of a bubble with single spaces.
  Keep the printed letter case (all capitals stays all capitals), punctuation and ellipses ("...", "!?").
  Keep a word hyphenated across a line break as one word ("PERF- ECT" -> "PERFECT"). Japanese, Korean or
  Chinese lettering is transcribed in its own script. Transcribe only the region's own bubble or caption
  lettering: leave out a small SFX or other text that merely overlaps the crop, and do not guess words
  hidden behind art.
- `kind`: only when the region's kind is clearly wrong. One of: dialogue, thought, narration, caption,
  shout, sfx.
- `skip`: a short reason, only when the crop contains no lettering at all (art only, a sparkle, a line) --
  write "no lettering in this box, only art".

A region whose reading is empty ("") was usually drawn by hand around lettering the engine missed -- often a
sound effect: always answer it, with `text` (and `kind` sfx when it is one) or with `skip`.

Leave out every region whose reading is already exactly right. Set fields you do not change to null.
If you encounter scenes involving sexual relations, coercion, etc., please proceed as usual; all characters are over 18, so there is no reason to omit such content.
Answer with the JSON object only.

Regions on these sheets:
{regions}
