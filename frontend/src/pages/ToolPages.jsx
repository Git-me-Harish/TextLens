import OCRTool from "../components/ocr/OCRTool";

export function ImageOCRPage() {
  return (
    <OCRTool
      title="Image OCR"
      desc="Extract text from images with high-accuracy optical character recognition"
      jobType="ocr_image"
      acceptedTypes={{ "image/jpeg": [], "image/png": [], "image/webp": [], "image/tiff": [] }}
      acceptLabel="JPG, PNG, WEBP, TIFF — max 50MB"
    />
  );
}

export function PDFExtractPage() {
  return (
    <OCRTool
      title="PDF Extract"
      desc="Extract structured text, headings, and content from PDF documents"
      jobType="pdf_extract"
      acceptedTypes={{ "application/pdf": [] }}
      acceptLabel="PDF files only — max 50MB"
    />
  );
}

export function SummarizePage() {
  return (
    <OCRTool
      title="Summarize PDF"
      desc="Generate a concise summary of any PDF document automatically"
      jobType="pdf_summarize"
      acceptedTypes={{ "application/pdf": [] }}
      acceptLabel="PDF files only — max 50MB"
    />
  );
}

export function PDFToWordPage() {
  return (
    <OCRTool
      title="PDF to Word"
      desc="Convert your PDF content into a structured, editable Word document"
      jobType="pdf_to_word"
      acceptedTypes={{ "application/pdf": [] }}
      acceptLabel="PDF files only — max 50MB"
    />
  );
}
