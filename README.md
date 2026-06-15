# FrameSyncPro (Web)

A web version of the FrameSyncPro desktop tool. Upload a video, extract frames
at a fixed interval, then draw and edit YOLO-style bounding-box annotations in
the browser. Originally a Tkinter desktop app, now a Flask web application.

## Features

- **Frame extraction** — upload a video (`.mp4 .avi .mkv .mov`) and extract a
  frame every N seconds using OpenCV. Progress is shown live.
- **Annotation viewer** — browse extracted images with a counter and prev/next
  navigation (also arrow keys ← →).
- **Draw mode** — click & drag on the image to create bounding boxes tagged with
  the current "Active Tag".
- **Edit mode** — click a box to select it (turns white), drag the center to
  move or drag a corner handle to resize. Rename via "Update Tag", remove via
  "Delete Box".
- **Delete image** — removes the image and its label file.
- Annotations are saved automatically to `data/<folder>/labels/<name>.txt` in
  normalized YOLO format: `tag x_center y_center width height`.
- **Load history** — reopen any previously extracted folder.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

Extracted frames and labels are written to `data/<folder>/` next to the app.

## Data layout

```
data/
  my_folder/
    frame_0000.jpg
    frame_0001.jpg
    labels/
      frame_0000.txt
      frame_0001.txt
```

## Notes on the label format

Labels keep the original FrameSyncPro convention of storing the **tag name**
(not a numeric class id) as the first field. To train a standard YOLO model you
will need to map tag names to integer class ids and emit a `classes.txt`. This
keeps full compatibility with files produced by the original desktop tool.
