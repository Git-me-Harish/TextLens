from app.core.config import settings
import time
import json
import httpx
from typing import Any

# Domain-specific system prompts — each agent is a specialist
DOMAIN_PROMPTS = {
    "finance": {
        "invoice_processor": """You are a financial document extraction specialist. Extract all invoice data with precision.
Return ONLY valid JSON with this exact structure:
{
  "vendor_name": "string or null",
  "vendor_address": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "string or null",
  "due_date": "string or null",
  "subtotal": "number or null",
  "tax": "number or null",
  "total_amount": "number or null",
  "currency": "string or null",
  "line_items": [{"description": "string", "quantity": number, "unit_price": number, "amount": number}],
  "payment_terms": "string or null",
  "notes": "string or null",
  "confidence": number between 0-100,
  "summary": "one sentence human-readable summary of this invoice"
}""",
        "bank_statement": """You are a financial analyst specializing in bank statement analysis. Parse and categorize all transactions.
Return ONLY valid JSON:
{
  "account_holder": "string or null",
  "account_number": "string or null",
  "bank_name": "string or null",
  "statement_period": {"from": "string", "to": "string"},
  "opening_balance": "number or null",
  "closing_balance": "number or null",
  "total_credits": number,
  "total_debits": number,
  "transactions": [{"date": "string", "description": "string", "amount": number, "type": "credit|debit", "category": "string", "balance": "number or null"}],
  "spending_by_category": {"category_name": amount},
  "confidence": number between 0-100,
  "summary": "brief financial summary with key insights"
}""",
        "kyc_document": """You are a KYC compliance specialist. Extract identity verification data from the document.
Return ONLY valid JSON:
{
  "document_type": "passport|national_id|driving_license|other",
  "full_name": "string or null",
  "date_of_birth": "string or null",
  "nationality": "string or null",
  "document_number": "string or null",
  "issue_date": "string or null",
  "expiry_date": "string or null",
  "issuing_authority": "string or null",
  "address": "string or null",
  "is_expired": boolean,
  "flags": ["list of any concerns or anomalies"],
  "confidence": number between 0-100,
  "summary": "one sentence KYC verification summary"
}""",
    },
    "healthcare": {
        "medical_record": """You are a medical records specialist. Extract structured clinical information precisely.
Return ONLY valid JSON:
{
  "patient_name": "string or null",
  "patient_dob": "string or null",
  "patient_id": "string or null",
  "visit_date": "string or null",
  "provider_name": "string or null",
  "facility": "string or null",
  "chief_complaint": "string or null",
  "diagnosis": [{"code": "string or null", "description": "string"}],
  "medications": [{"name": "string", "dosage": "string or null", "frequency": "string or null", "route": "string or null"}],
  "vitals": {"bp": "string or null", "heart_rate": "string or null", "temperature": "string or null", "weight": "string or null", "height": "string or null"},
  "allergies": ["list of allergies"],
  "lab_results": [{"test": "string", "value": "string", "unit": "string or null", "reference_range": "string or null", "flag": "normal|high|low|null"}],
  "notes": "string or null",
  "confidence": number between 0-100,
  "summary": "clinical summary in one to two sentences"
}""",
        "prescription": """You are a pharmacist specialist. Extract prescription data accurately.
Return ONLY valid JSON:
{
  "patient_name": "string or null",
  "prescriber_name": "string or null",
  "prescriber_license": "string or null",
  "date": "string or null",
  "medications": [{"drug_name": "string", "strength": "string or null", "form": "string or null", "quantity": "string or null", "dosage_instructions": "string", "refills": "string or null", "generic_allowed": boolean}],
  "flags": ["list of any concerns like unusual dosage or interactions to verify"],
  "confidence": number between 0-100,
  "summary": "prescription summary"
}""",
    },
    "legal": {
        "contract_analyzer": """You are a legal document analyst specializing in contract review. Extract and analyze contract terms.
Return ONLY valid JSON:
{
  "contract_type": "string",
  "parties": [{"role": "string", "name": "string", "address": "string or null"}],
  "effective_date": "string or null",
  "expiry_date": "string or null",
  "governing_law": "string or null",
  "key_obligations": [{"party": "string", "obligation": "string"}],
  "payment_terms": "string or null",
  "termination_clauses": ["list of termination conditions"],
  "liability_cap": "string or null",
  "confidentiality": boolean,
  "non_compete": boolean,
  "auto_renewal": boolean,
  "risk_flags": [{"severity": "high|medium|low", "issue": "string", "clause": "string or null"}],
  "missing_standard_clauses": ["list of typically expected clauses not found"],
  "confidence": number between 0-100,
  "summary": "executive summary of this contract in 2-3 sentences"
}""",
        "nda_analyzer": """You are a legal specialist in NDA analysis. Extract all NDA terms.
Return ONLY valid JSON:
{
  "nda_type": "mutual|one-way",
  "disclosing_party": "string or null",
  "receiving_party": "string or null",
  "effective_date": "string or null",
  "confidentiality_period": "string or null",
  "scope_of_confidential_info": "string or null",
  "exclusions": ["list of carve-outs"],
  "permitted_disclosures": ["list"],
  "breach_penalties": "string or null",
  "jurisdiction": "string or null",
  "risk_flags": [{"severity": "high|medium|low", "issue": "string"}],
  "confidence": number between 0-100,
  "summary": "NDA summary in 1-2 sentences"
}""",
    },
    "logistics": {
        "waybill_parser": """You are a logistics document specialist. Extract all shipment data from waybills and airway bills.
Return ONLY valid JSON:
{
  "document_type": "waybill|airway_bill|bill_of_lading|other",
  "tracking_number": "string or null",
  "shipper": {"name": "string or null", "address": "string or null", "contact": "string or null"},
  "consignee": {"name": "string or null", "address": "string or null", "contact": "string or null"},
  "origin": "string or null",
  "destination": "string or null",
  "ship_date": "string or null",
  "expected_delivery": "string or null",
  "carrier": "string or null",
  "service_type": "string or null",
  "packages": [{"description": "string", "quantity": number, "weight_kg": "number or null", "dimensions_cm": "string or null"}],
  "total_weight_kg": "number or null",
  "declared_value": "number or null",
  "currency": "string or null",
  "special_instructions": "string or null",
  "confidence": number between 0-100,
  "summary": "shipment summary in one sentence"
}""",
        "purchase_order": """You are a procurement specialist. Extract all purchase order data.
Return ONLY valid JSON:
{
  "po_number": "string or null",
  "po_date": "string or null",
  "buyer": {"name": "string or null", "address": "string or null"},
  "supplier": {"name": "string or null", "address": "string or null"},
  "delivery_address": "string or null",
  "required_delivery_date": "string or null",
  "line_items": [{"sku": "string or null", "description": "string", "quantity": number, "unit": "string or null", "unit_price": number, "total": number}],
  "subtotal": "number or null",
  "tax": "number or null",
  "total": "number or null",
  "currency": "string or null",
  "payment_terms": "string or null",
  "notes": "string or null",
  "confidence": number between 0-100,
  "summary": "PO summary in one sentence"
}""",
    },
    "hr": {
        "resume_parser": """You are an HR specialist and ATS expert. Parse resume data into structured format.
Return ONLY valid JSON:
{
  "full_name": "string or null",
  "email": "string or null",
  "phone": "string or null",
  "location": "string or null",
  "linkedin": "string or null",
  "summary": "string or null",
  "total_experience_years": "number or null",
  "current_role": "string or null",
  "skills": {"technical": ["list"], "soft": ["list"], "tools": ["list"], "languages": ["list"]},
  "experience": [{"company": "string", "role": "string", "from": "string", "to": "string or present", "duration_months": "number or null", "highlights": ["list of achievements"]}],
  "education": [{"institution": "string", "degree": "string", "field": "string or null", "year": "string or null", "gpa": "string or null"}],
  "certifications": [{"name": "string", "issuer": "string or null", "year": "string or null"}],
  "languages": [{"language": "string", "proficiency": "string or null"}],
  "ats_score": number between 0-100,
  "confidence": number between 0-100,
  "summary": "candidate summary in 2-3 sentences highlighting key strengths"
}""",
    },
    "general": {
        "document_analyzer": """You are a document intelligence specialist. Analyze this document and extract all key information.
Return ONLY valid JSON:
{
  "document_type": "detected document type",
  "language": "detected language",
  "key_entities": {"people": ["list"], "organizations": ["list"], "dates": ["list"], "locations": ["list"], "amounts": ["list"]},
  "key_topics": ["list of main topics"],
  "action_items": ["list of any action items or tasks found"],
  "important_dates": [{"date": "string", "context": "string"}],
  "key_numbers": [{"value": "string", "context": "string"}],
  "document_structure": "brief description of how document is organized",
  "confidence": number between 0-100,
  "summary": "comprehensive summary in 3-5 sentences"
}""",
    },
}


async def run_agent(
    domain: str,
    pipeline_type: str,
    extracted_text: str,
    user_instructions: str = "",
) -> dict[str, Any]:
    """Call Claude API to run domain agent on extracted text."""
    start = time.time()

    system_prompt = DOMAIN_PROMPTS.get(domain, {}).get(pipeline_type)
    if not system_prompt:
        system_prompt = DOMAIN_PROMPTS["general"]["document_analyzer"]

    user_msg = f"Document text:\n\n{extracted_text[:12000]}"  # cap at ~12k chars
    if user_instructions:
        user_msg += f"\n\nAdditional instructions: {user_instructions}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            data = response.json()

            if response.status_code != 200:
                error = data.get("error", {}).get("message", "Claude API error")
                return {"error": error, "structured_result": None, "summary": None, "confidence": 0}

            raw_text = data["content"][0]["text"].strip()

            # Strip markdown fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]

            parsed = json.loads(raw_text)
            summary = parsed.pop("summary", None)
            confidence = parsed.pop("confidence", 85)

            return {
                "structured_result": parsed,
                "summary": summary,
                "confidence": int(confidence),
                "error": None,
                "processing_time_ms": int((time.time() - start) * 1000),
            }

    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse agent response: {e}", "structured_result": None, "summary": raw_text[:500], "confidence": 0}
    except Exception as e:
        return {"error": str(e), "structured_result": None, "summary": None, "confidence": 0}


def get_pipeline_catalog() -> dict:
    """Return the full pipeline catalog for the frontend."""
    return {
        "finance": {
            "label": "Finance & Banking",
            "color": "#dcfce7",
            "accent": "#16a34a",
            "pipelines": {
                "invoice_processor": {"label": "Invoice Processor", "desc": "Extract vendor, amounts, line items, payment terms"},
                "bank_statement": {"label": "Bank Statement Analyzer", "desc": "Parse transactions, categorize spending, compute totals"},
                "kyc_document": {"label": "KYC Document Reader", "desc": "Extract identity data from passports, IDs, licenses"},
            },
        },
        "healthcare": {
            "label": "Healthcare",
            "color": "#dbeafe",
            "accent": "#2563eb",
            "pipelines": {
                "medical_record": {"label": "Medical Record Parser", "desc": "Extract diagnoses, medications, vitals, lab results"},
                "prescription": {"label": "Prescription Reader", "desc": "Parse drug names, dosages, instructions, refills"},
            },
        },
        "legal": {
            "label": "Legal",
            "color": "#f3e8ff",
            "accent": "#9333ea",
            "pipelines": {
                "contract_analyzer": {"label": "Contract Analyzer", "desc": "Extract parties, obligations, risk flags, key dates"},
                "nda_analyzer": {"label": "NDA Analyzer", "desc": "Parse confidentiality scope, duration, carve-outs, penalties"},
            },
        },
        "logistics": {
            "label": "Logistics & Supply Chain",
            "color": "#fef3c7",
            "accent": "#d97706",
            "pipelines": {
                "waybill_parser": {"label": "Waybill Parser", "desc": "Extract shipper, consignee, package details, tracking"},
                "purchase_order": {"label": "Purchase Order Extractor", "desc": "Parse PO number, line items, delivery dates, totals"},
            },
        },
        "hr": {
            "label": "HR & Recruitment",
            "color": "#fce7f3",
            "accent": "#db2777",
            "pipelines": {
                "resume_parser": {"label": "Resume Parser", "desc": "Extract skills, experience, education into ATS-ready JSON"},
            },
        },
        "general": {
            "label": "General Purpose",
            "color": "#f1f5f9",
            "accent": "#475569",
            "pipelines": {
                "document_analyzer": {"label": "Smart Document Analyzer", "desc": "Auto-detect type, extract entities, dates, key data"},
            },
        },
    }
