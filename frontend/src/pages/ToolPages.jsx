import { Navigate } from "react-router-dom";
export function ImageOCRPage() {
  return <Navigate to="/tools/extract" replace />;
}

export function PDFExtractPage() {
  return <Navigate to="/tools/extract" replace />;
}

export function PDFToWordPage() {
  return <Navigate to="/tools/studio" replace />;
}

export { default as SummarizePage } from "./SummarizePage";