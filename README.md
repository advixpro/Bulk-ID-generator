# ID Card Studio

A local Windows browser app that merges a PDF template, Excel records, and employee photos into individual PDFs.

## Start the app

Double-click **Start ID Card Studio.cmd**. Keep the window open while you work. The app opens in your browser at http://127.0.0.1:8765.

The launcher uses the bundled Python runtime when available. Otherwise it needs Python 3.11 or later and installs the dependencies into a project-local environment on first launch. The first installation may need internet access. The app itself runs on this PC.

## Create a batch

1. **Choose PDF template.** For a multipage template, assign the front and optional back. The selected pages appear one below the other.
2. **Choose Excel workbook.** The first row should contain column names. Select the worksheet and the column that matches photo filenames. Employee IDs stored as text or with a simple zero-padded number format keep their leading zeros.
3. **Choose photo folder.** Put the JPG, JPEG, and PNG files directly in that folder. The tool compares decoded pixels after applying orientation. Review identical groups as prompted and choose the files to keep. The first file in each group is determined by alphabetical filename order.
4. **Draw and style fields.** Choose **Text**, **Photo**, or **Cover Area**, then drag a box on the card. Use **Select** to move a box or drag its corner to resize. Text accepts variables such as {Name AR} - {Name EN}. Photo presets start at their listed dimensions, scaled down proportionally if they exceed the page. You can change the ratio, choose Fill or Fit, and round corners. Covers go beneath all text and photos. The **Pick** button samples a color from the original template.
5. **Preview card.** Select a sample from the first 20 records. The preview renders the same PDF the exporter creates. Long text shrinks to the field's minimum size. If it still overflows, the record is reported and skipped.
6. **Choose output folder** and click **Generate PDFs**. Use {Employee ID} for a column, #### for a zero-padded sequence, and ordinary text for constants. For example, pre-#### produces pre-0001.pdf from a start of 1. Skipped rows reserve their sequence numbers. Existing files receive a suffix such as _2. The summary includes a **Save Detailed Report** button.

Use **Save Setup** to save the template, selected pages, layout, styles, worksheet and photo mapping, and naming settings. **Open Setup** lists saved setups. Choose the current spreadsheet, photo folder, and output folder for each new batch. Windows stores setups under your local AppData folder in ID Card Studio.

Each photo field has optional **Auto Framing** and **Use off-white background** settings. Auto Framing centers the head in a tight head-and-shoulders crop, with a small margin above the hair or head covering. It uses the existing photo box proportions. If it cannot make a clear crop, it uses the field's Fill or Fit setting. Background replacement keeps the original background when the person cannot be separated confidently. Cards that use either fallback are exported to **For Review** inside the selected output folder. Preview shows the warning, and the Excel report lists each affected card's reason and location. The models run locally. [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) and [PP-HumanSeg](https://github.com/opencv/opencv_zoo/tree/main/models/human_segmentation_pphumanseg) license files are in the models folder.

The PDF artwork remains in its original PDF format. Text added by the editor is rendered at 360 DPI for consistent Arabic shaping and bidirectional layout. Standard PDF form templates are treated as artwork with their existing form appearances flattened before overlay placement. For XFA forms, start with a regular PDF copy. Cover boxes visually mask content. Use a blank template when the original sample information must be removed from the file itself.

**Crop camera watermarks** is a separate photo setting. It uses local Tesseract OCR with English language data to look for recognizable camera branding such as Redmi or AI Quad Camera in the outer bands of a photo. It trims the affected edge when it can verify the crop keeps the head intact. A date or time alone does not trigger trimming, but may be in the trimmed area. The app looks for Tesseract on PATH and in the standard Windows installation folder. If OCR is unavailable or a detected brand cannot be safely cropped, the card goes to **For Review**.

For Review PDFs and their original photos are saved directly in the same folder. An identical file is reused. If a different photo already has the same name, the new copy gets a suffix. The detailed Excel report lists the saved original photo name.

## Try it with the sample workbook

Open **ID Card Test Employees.xlsx**, choose **Employee ID** as the photo matching column, and name your photos 00101.jpg through 00110.jpg (PNG and JPEG also work). The workbook includes long bilingual names, fictional national IDs, and example phone numbers.
