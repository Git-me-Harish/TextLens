"""
Agent service — Claude API integration for all domain pipelines.

Phase 2 additions:
  - Education & HR pipelines (transcript, certificate, answer sheet)
  - Government & Compliance (tax, permit, SEC)
  - Expanded Finance (cheque, financial report)
  - Expanded Healthcare (lab report, insurance claim)
  - Expanded Legal (court doc, due diligence)
  - Expanded Logistics (customs, packing list)
  - Domain auto-classifier
"""
import time
import json
import httpx
from typing import Any

from app.core.config import settings

# ─────────────────────────────────────────────────────────────────────
# System prompts — each is a specialist agent with typed JSON contract
# ─────────────────────────────────────────────────────────────────────

DOMAIN_PROMPTS: dict[str, dict[str, str]] = {

    # ── FINANCE ──────────────────────────────────────────────────────
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
  "duplicate_risk": "low|medium|high — flag if invoice number or amount matches typical duplicates",
  "anomaly_flags": ["list any unusual amounts, missing fields, or suspicious patterns"],
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
  "transactions": [{"date": "string", "description": "string", "amount": number, "type": "credit|debit", "category": "salary|rent|food|utilities|entertainment|transfer|other", "balance": "number or null"}],
  "spending_by_category": {"category_name": amount},
  "top_merchants": [{"name": "string", "total": number, "count": number}],
  "confidence": number between 0-100,
  "summary": "brief financial summary with key insights about spending patterns"
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

        "cheque_parser": """You are a banking specialist. Extract all data from this cheque/check image.
Return ONLY valid JSON:
{
  "payee_name": "string or null",
  "amount_numeric": "number or null",
  "amount_words": "string or null",
  "amounts_match": boolean,
  "date": "string or null",
  "bank_name": "string or null",
  "account_number": "string or null",
  "routing_number": "string or null",
  "cheque_number": "string or null",
  "memo": "string or null",
  "signature_present": boolean,
  "flags": ["any discrepancies between numeric and written amounts, missing fields, etc."],
  "confidence": number between 0-100,
  "summary": "one sentence cheque summary"
}""",

        "financial_report": """You are a financial analyst. Extract and summarize key metrics from this financial report (P&L, balance sheet, cash flow).
Return ONLY valid JSON:
{
  "company_name": "string or null",
  "report_type": "P&L|balance_sheet|cash_flow|annual_report|other",
  "period": "string or null",
  "currency": "string or null",
  "revenue": "number or null",
  "gross_profit": "number or null",
  "operating_income": "number or null",
  "net_income": "number or null",
  "total_assets": "number or null",
  "total_liabilities": "number or null",
  "equity": "number or null",
  "key_ratios": {"gross_margin": "string or null", "net_margin": "string or null", "current_ratio": "string or null", "debt_to_equity": "string or null"},
  "trend_commentary": "qualitative observation on trends visible in this report",
  "risk_flags": ["any concerning metrics or anomalies"],
  "confidence": number between 0-100,
  "summary": "executive financial summary in 2-3 sentences"
}""",
    },

    # ── HEALTHCARE ───────────────────────────────────────────────────
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
  "diagnosis": [{"code": "ICD-10 code or null", "description": "string"}],
  "medications": [{"name": "string", "dosage": "string or null", "frequency": "string or null", "route": "string or null"}],
  "vitals": {"bp": "string or null", "heart_rate": "string or null", "temperature": "string or null", "weight": "string or null", "height": "string or null", "spo2": "string or null"},
  "allergies": ["list of allergies"],
  "lab_results": [{"test": "string", "value": "string", "unit": "string or null", "reference_range": "string or null", "flag": "normal|high|low|critical|null"}],
  "follow_up": "string or null",
  "notes": "string or null",
  "confidence": number between 0-100,
  "summary": "clinical summary in 1-2 sentences"
}""",

        "prescription": """You are a pharmacist specialist. Extract prescription data accurately.
Return ONLY valid JSON:
{
  "patient_name": "string or null",
  "prescriber_name": "string or null",
  "prescriber_license": "string or null",
  "prescriber_specialty": "string or null",
  "date": "string or null",
  "medications": [{"drug_name": "string", "strength": "string or null", "form": "tablet|capsule|liquid|injection|topical|other", "quantity": "string or null", "dosage_instructions": "string", "refills": "string or null", "generic_allowed": boolean}],
  "flags": ["unusual dosage, potential interactions, missing required fields"],
  "confidence": number between 0-100,
  "summary": "prescription summary"
}""",

        "lab_report": """You are a clinical laboratory specialist. Extract and flag all lab test results.
Return ONLY valid JSON:
{
  "patient_name": "string or null",
  "patient_dob": "string or null",
  "lab_name": "string or null",
  "ordering_physician": "string or null",
  "collection_date": "string or null",
  "report_date": "string or null",
  "specimen_type": "string or null",
  "results": [{"test_name": "string", "value": "string", "unit": "string or null", "reference_range": "string or null", "flag": "normal|high|low|critical|null", "interpretation": "string or null"}],
  "abnormal_results": ["list of tests that are outside reference range"],
  "critical_values": ["list of any critical/panic values requiring immediate action"],
  "confidence": number between 0-100,
  "summary": "lab report summary highlighting abnormal findings"
}""",

        "insurance_claim": """You are a medical billing specialist. Extract insurance claim data.
Return ONLY valid JSON:
{
  "claim_number": "string or null",
  "patient_name": "string or null",
  "patient_dob": "string or null",
  "insurance_provider": "string or null",
  "policy_number": "string or null",
  "group_number": "string or null",
  "provider_name": "string or null",
  "provider_npi": "string or null",
  "service_date": "string or null",
  "diagnosis_codes": ["ICD-10 codes"],
  "procedure_codes": [{"cpt_code": "string", "description": "string or null", "units": number, "charge": "number or null"}],
  "total_charges": "number or null",
  "amount_billed": "number or null",
  "claim_status": "string or null",
  "flags": ["missing codes, invalid combinations, billing anomalies"],
  "confidence": number between 0-100,
  "summary": "claim summary in one sentence"
}""",
    },

    # ── LEGAL ────────────────────────────────────────────────────────
    "legal": {
        "contract_analyzer": """You are a legal document analyst specializing in contract review. Extract and analyze contract terms.
Return ONLY valid JSON:
{
  "contract_type": "string",
  "parties": [{"role": "string", "name": "string", "address": "string or null"}],
  "effective_date": "string or null",
  "expiry_date": "string or null",
  "governing_law": "string or null",
  "jurisdiction": "string or null",
  "key_obligations": [{"party": "string", "obligation": "string", "deadline": "string or null"}],
  "payment_terms": "string or null",
  "termination_clauses": ["list of termination conditions"],
  "liability_cap": "string or null",
  "indemnification": "string or null",
  "dispute_resolution": "arbitration|litigation|mediation|null",
  "confidentiality": boolean,
  "non_compete": boolean,
  "non_solicitation": boolean,
  "auto_renewal": boolean,
  "ip_ownership": "string or null",
  "risk_flags": [{"severity": "high|medium|low", "issue": "string", "clause": "string or null"}],
  "missing_standard_clauses": ["list of typically expected clauses not found"],
  "key_dates": [{"date": "string", "event": "string"}],
  "confidence": number between 0-100,
  "summary": "executive summary in 2-3 sentences"
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
  "exclusions": ["list of carve-outs from confidentiality"],
  "permitted_disclosures": ["list of allowed disclosure scenarios"],
  "return_of_information": "string or null",
  "breach_penalties": "string or null",
  "jurisdiction": "string or null",
  "risk_flags": [{"severity": "high|medium|low", "issue": "string"}],
  "confidence": number between 0-100,
  "summary": "NDA summary in 1-2 sentences"
}""",

        "court_document": """You are a legal specialist in court document analysis.
Return ONLY valid JSON:
{
  "document_type": "complaint|motion|order|judgment|subpoena|other",
  "case_number": "string or null",
  "court_name": "string or null",
  "jurisdiction": "string or null",
  "judge": "string or null",
  "plaintiff": "string or null",
  "defendant": "string or null",
  "filing_date": "string or null",
  "hearing_date": "string or null",
  "case_status": "string or null",
  "claims": ["list of legal claims or causes of action"],
  "relief_sought": "string or null",
  "key_dates": [{"date": "string", "event": "string"}],
  "deadlines": [{"date": "string", "action": "string"}],
  "confidence": number between 0-100,
  "summary": "case summary in 2-3 sentences"
}""",

        "due_diligence": """You are a senior M&A lawyer conducting due diligence document review. Analyze this document for risk.
Return ONLY valid JSON:
{
  "document_type": "string",
  "parties_identified": ["list"],
  "key_dates": [{"date": "string", "event": "string"}],
  "financial_obligations": ["list of financial commitments found"],
  "ip_references": ["any IP, patent, trademark mentions"],
  "litigation_references": ["any litigation, dispute, claim mentions"],
  "regulatory_references": ["any regulatory, compliance, license mentions"],
  "change_of_control_clauses": boolean,
  "assignment_restrictions": boolean,
  "risk_flags": [{"severity": "high|medium|low", "category": "financial|legal|operational|regulatory", "issue": "string", "page_reference": "string or null"}],
  "missing_standard_elements": ["typically expected elements not found"],
  "confidence": number between 0-100,
  "summary": "due diligence assessment in 2-3 sentences"
}""",
    },

    # ── LOGISTICS ────────────────────────────────────────────────────
    "logistics": {
        "waybill_parser": """You are a logistics document specialist. Extract all shipment data from waybills and airway bills.
Return ONLY valid JSON:
{
  "document_type": "waybill|airway_bill|bill_of_lading|other",
  "tracking_number": "string or null",
  "shipper": {"name": "string or null", "address": "string or null", "contact": "string or null"},
  "consignee": {"name": "string or null", "address": "string or null", "contact": "string or null"},
  "notify_party": "string or null",
  "origin": "string or null",
  "destination": "string or null",
  "ship_date": "string or null",
  "expected_delivery": "string or null",
  "carrier": "string or null",
  "service_type": "string or null",
  "packages": [{"description": "string", "quantity": number, "weight_kg": "number or null", "dimensions_cm": "string or null", "value": "number or null"}],
  "total_weight_kg": "number or null",
  "volumetric_weight_kg": "number or null",
  "declared_value": "number or null",
  "currency": "string or null",
  "incoterms": "string or null",
  "special_instructions": "string or null",
  "confidence": number between 0-100,
  "summary": "shipment summary in one sentence"
}""",

        "purchase_order": """You are a procurement specialist. Extract all purchase order data.
Return ONLY valid JSON:
{
  "po_number": "string or null",
  "po_date": "string or null",
  "buyer": {"name": "string or null", "address": "string or null", "contact": "string or null"},
  "supplier": {"name": "string or null", "address": "string or null", "contact": "string or null"},
  "delivery_address": "string or null",
  "required_delivery_date": "string or null",
  "line_items": [{"sku": "string or null", "description": "string", "quantity": number, "unit": "string or null", "unit_price": number, "total": number}],
  "subtotal": "number or null",
  "tax": "number or null",
  "shipping": "number or null",
  "total": "number or null",
  "currency": "string or null",
  "payment_terms": "string or null",
  "notes": "string or null",
  "confidence": number between 0-100,
  "summary": "PO summary in one sentence"
}""",

        "customs_declaration": """You are a customs and trade compliance specialist.
Return ONLY valid JSON:
{
  "declaration_type": "import|export|transit",
  "declaration_number": "string or null",
  "declarant": "string or null",
  "importer": "string or null",
  "exporter": "string or null",
  "country_of_origin": "string or null",
  "country_of_destination": "string or null",
  "port_of_entry": "string or null",
  "declaration_date": "string or null",
  "line_items": [{"hs_code": "string or null", "description": "string", "quantity": number, "unit": "string or null", "declared_value": number, "currency": "string or null", "weight_kg": "number or null"}],
  "total_declared_value": "number or null",
  "duty_amount": "number or null",
  "tax_amount": "number or null",
  "flags": ["any HS code anomalies, undervaluation risk, missing certificates"],
  "confidence": number between 0-100,
  "summary": "customs declaration summary"
}""",

        "packing_list": """You are a warehouse and logistics specialist.
Return ONLY valid JSON:
{
  "packing_list_number": "string or null",
  "shipper": "string or null",
  "consignee": "string or null",
  "po_reference": "string or null",
  "ship_date": "string or null",
  "items": [{"sku": "string or null", "description": "string", "quantity": number, "unit": "string or null", "weight_per_unit_kg": "number or null", "total_weight_kg": "number or null", "batch_number": "string or null", "box_number": "string or null"}],
  "total_cartons": "number or null",
  "total_gross_weight_kg": "number or null",
  "total_net_weight_kg": "number or null",
  "total_volume_cbm": "number or null",
  "confidence": number between 0-100,
  "summary": "packing list summary in one sentence"
}""",
    },

    # ── HR & EDUCATION ───────────────────────────────────────────────
    "hr": {
        "resume_parser": """You are an HR specialist and ATS expert. Parse resume data into structured format.
Return ONLY valid JSON:
{
  "full_name": "string or null",
  "email": "string or null",
  "phone": "string or null",
  "location": "string or null",
  "linkedin": "string or null",
  "github": "string or null",
  "portfolio": "string or null",
  "summary": "string or null",
  "total_experience_years": "number or null",
  "current_role": "string or null",
  "skills": {"technical": ["list"], "soft": ["list"], "tools": ["list"], "languages": ["list"]},
  "experience": [{"company": "string", "role": "string", "from": "string", "to": "string or present", "duration_months": "number or null", "highlights": ["list of quantified achievements"]}],
  "education": [{"institution": "string", "degree": "string", "field": "string or null", "year": "string or null", "gpa": "string or null"}],
  "certifications": [{"name": "string", "issuer": "string or null", "year": "string or null", "expiry": "string or null"}],
  "languages": [{"language": "string", "proficiency": "native|fluent|professional|conversational|basic"}],
  "awards": ["list"],
  "publications": ["list"],
  "ats_score": number between 0-100,
  "ats_recommendations": ["list of improvements to increase ATS match"],
  "confidence": number between 0-100,
  "summary": "candidate summary in 2-3 sentences highlighting key strengths"
}""",

        "certificate_verifier": """You are a credential verification specialist. Extract certificate details.
Return ONLY valid JSON:
{
  "certificate_type": "academic|professional|training|award|other",
  "title": "string or null",
  "recipient_name": "string or null",
  "issuing_institution": "string or null",
  "issue_date": "string or null",
  "expiry_date": "string or null",
  "certificate_number": "string or null",
  "grade_or_score": "string or null",
  "field_or_course": "string or null",
  "is_expired": boolean,
  "verification_url": "string or null",
  "confidence": number between 0-100,
  "summary": "certificate summary in one sentence"
}""",

        "transcript_analyzer": """You are an academic records specialist. Extract and analyze academic transcripts.
Return ONLY valid JSON:
{
  "student_name": "string or null",
  "student_id": "string or null",
  "institution": "string or null",
  "program": "string or null",
  "degree": "string or null",
  "enrollment_date": "string or null",
  "graduation_date": "string or null",
  "courses": [{"code": "string or null", "name": "string", "credits": "number or null", "grade": "string or null", "grade_points": "number or null", "semester": "string or null"}],
  "total_credits": "number or null",
  "gpa": "number or null",
  "honors": "string or null",
  "academic_standing": "good_standing|probation|honors|null",
  "confidence": number between 0-100,
  "summary": "academic performance summary in 2 sentences"
}""",
    },

    # ── GOVERNMENT & COMPLIANCE ──────────────────────────────────────
    "government": {
        "tax_form": """You are a tax document specialist. Extract all fields from this tax form.
Return ONLY valid JSON:
{
  "form_type": "W2|1099-NEC|1099-INT|1099-DIV|1040|ITR|other",
  "tax_year": "string or null",
  "taxpayer_name": "string or null",
  "taxpayer_ssn_last4": "string or null — last 4 digits only for privacy",
  "employer_name": "string or null",
  "employer_ein": "string or null",
  "wages_salaries": "number or null",
  "federal_tax_withheld": "number or null",
  "state_tax_withheld": "number or null",
  "social_security_wages": "number or null",
  "medicare_wages": "number or null",
  "other_income": "number or null",
  "deductions": "number or null",
  "taxable_income": "number or null",
  "tax_due": "number or null",
  "refund": "number or null",
  "flags": ["missing fields, arithmetic discrepancies"],
  "confidence": number between 0-100,
  "summary": "tax form summary in one sentence"
}""",

        "permit_license": """You are a regulatory document specialist. Extract permit and license details.
Return ONLY valid JSON:
{
  "document_type": "business_license|building_permit|operating_permit|professional_license|other",
  "license_number": "string or null",
  "holder_name": "string or null",
  "holder_address": "string or null",
  "issuing_authority": "string or null",
  "issue_date": "string or null",
  "expiry_date": "string or null",
  "is_expired": boolean,
  "scope": "string or null",
  "conditions": ["list of conditions or restrictions"],
  "fee_paid": "number or null",
  "renewal_required": boolean,
  "confidence": number between 0-100,
  "summary": "permit/license summary in one sentence"
}""",

        "regulatory_filing": """You are a regulatory compliance specialist (SEC, SEBI, FCA filings).
Return ONLY valid JSON:
{
  "filing_type": "10-K|10-Q|8-K|S-1|proxy|other",
  "company_name": "string or null",
  "ticker": "string or null",
  "filing_date": "string or null",
  "period_of_report": "string or null",
  "registrant_cik": "string or null",
  "key_disclosures": ["list of material disclosures"],
  "financial_highlights": {"revenue": "string or null", "net_income": "string or null", "eps": "string or null", "total_assets": "string or null"},
  "risk_factors": ["top 5 risk factors mentioned"],
  "material_changes": ["any material changes from prior period"],
  "confidence": number between 0-100,
  "summary": "regulatory filing summary in 2-3 sentences"
}""",
    },

    # ── GENERAL ──────────────────────────────────────────────────────
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

        "auto_classify": """You are a document classification specialist.
Analyze the document text and determine the most appropriate processing pipeline.
Return ONLY valid JSON:
{
  "detected_domain": "finance|healthcare|legal|logistics|hr|government|general",
  "detected_pipeline": "the most specific pipeline key that matches this document",
  "confidence": number between 0-100,
  "reasoning": "one sentence explaining the classification decision",
  "alternative": {"domain": "string", "pipeline": "string", "confidence": number} or null
}""",
    },
}


# ─────────────────────────────────────────────────────────────────────
# Domain classifier — auto-detects document type before routing
# ─────────────────────────────────────────────────────────────────────

async def classify_document(extracted_text: str) -> dict[str, Any]:
    """
    Use Claude to auto-classify document domain + pipeline.
    Returns: {domain, pipeline_type, confidence, reasoning}
    Falls back to general/document_analyzer on any failure.
    """
    fallback = {
        "detected_domain": "general",
        "detected_pipeline": "document_analyzer",
        "confidence": 50,
        "reasoning": "Auto-classification failed — using general analyzer",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "system": DOMAIN_PROMPTS["general"]["auto_classify"],
                    "messages": [{"role": "user", "content": f"Document text (first 3000 chars):\n\n{extracted_text[:3000]}"}],
                },
            )
            data = response.json()
            if response.status_code != 200:
                return fallback

            raw = data["content"][0]["text"].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            result = json.loads(raw)
            return result

    except Exception:
        return fallback


# ─────────────────────────────────────────────────────────────────────
# Core agent runner
# ─────────────────────────────────────────────────────────────────────

async def run_agent(
    domain: str,
    pipeline_type: str,
    extracted_text: str,
    user_instructions: str = "",
    db=None,   # optional AsyncSession — enables correction feedback injection
) -> dict[str, Any]:
    """
    Call Claude API to run domain agent on extracted text.

    Returns:
        structured_result: dict — extracted fields
        summary: str
        confidence: int (0-100)
        error: str | None
        processing_time_ms: int
    """
    start = time.time()

    system_prompt = DOMAIN_PROMPTS.get(domain, {}).get(pipeline_type)
    if not system_prompt:
        # Graceful fallback — never hard fail on unknown pipeline
        system_prompt = DOMAIN_PROMPTS["general"]["document_analyzer"]

    # Inject human correction examples if DB session provided
    if db is not None:
        try:
            from app.services.feedback_service import get_few_shot_examples
            few_shot = await get_few_shot_examples(db, domain, pipeline_type)
            if few_shot:
                system_prompt = system_prompt + few_shot
        except Exception:
            pass  # feedback is best-effort, never block the run

    user_msg = f"Document text:\n\n{extracted_text[:12000]}"
    if user_instructions:
        user_msg += f"\n\nAdditional instructions from user: {user_instructions}"

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4000,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            data = response.json()

            if response.status_code != 200:
                error_msg = data.get("error", {}).get("message", "Claude API error")
                return {
                    "error": error_msg,
                    "structured_result": None,
                    "summary": None,
                    "confidence": 0,
                    "processing_time_ms": int((time.time() - start) * 1000),
                }

            raw_text: str = data["content"][0]["text"].strip()

            # Strip markdown fences if model adds them despite instructions
            if raw_text.startswith("```"):
                parts = raw_text.split("```")
                raw_text = parts[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            parsed: dict = json.loads(raw_text)
            summary: str | None = parsed.pop("summary", None)
            confidence: int = int(parsed.pop("confidence", 85))

            return {
                "structured_result": parsed,
                "summary": summary,
                "confidence": confidence,
                "error": None,
                "processing_time_ms": int((time.time() - start) * 1000),
            }

    except json.JSONDecodeError as exc:
        # Return partial — summary from raw text is still useful
        raw_preview = locals().get("raw_text", "")[:500]
        return {
            "error": f"Failed to parse agent JSON response: {exc}",
            "structured_result": None,
            "summary": raw_preview or None,
            "confidence": 0,
            "processing_time_ms": int((time.time() - start) * 1000),
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "structured_result": None,
            "summary": None,
            "confidence": 0,
            "processing_time_ms": int((time.time() - start) * 1000),
        }


# ─────────────────────────────────────────────────────────────────────
# Pipeline catalog — drives frontend domain/pipeline selector UI
# ─────────────────────────────────────────────────────────────────────

def get_pipeline_catalog() -> dict:
    """Return the full pipeline catalog for the frontend selector."""
    return {
        "finance": {
            "label": "Finance & Banking",
            "color": "#dcfce7",
            "accent": "#16a34a",
            "icon": "💰",
            "pipelines": {
                "invoice_processor":  {"label": "Invoice Processor",        "desc": "Extract vendor, amounts, line items, payment terms, duplicate risk"},
                "bank_statement":     {"label": "Bank Statement Analyzer",  "desc": "Parse transactions, auto-categorize spending, compute totals"},
                "kyc_document":       {"label": "KYC Document Reader",      "desc": "Extract identity data from passports, IDs, driving licenses"},
                "cheque_parser":      {"label": "Cheque Parser",            "desc": "Payee, amount (numeric + words), bank, account number, validation"},
                "financial_report":   {"label": "Financial Report Summary", "desc": "P&L / balance sheet key metrics extraction with trend commentary"},
            },
        },
        "healthcare": {
            "label": "Healthcare",
            "color": "#dbeafe",
            "accent": "#2563eb",
            "icon": "🏥",
            "pipelines": {
                "medical_record":    {"label": "Medical Record Parser",    "desc": "Extract diagnoses (ICD-10), medications, vitals, lab results"},
                "prescription":      {"label": "Prescription Reader",      "desc": "Parse drug names, dosages, instructions, refills, flag anomalies"},
                "lab_report":        {"label": "Lab Report Analyzer",      "desc": "Extract test values, flag abnormal results and critical values"},
                "insurance_claim":   {"label": "Insurance Claim Extractor","desc": "Claim numbers, CPT procedure codes, amounts, billing flags"},
            },
        },
        "legal": {
            "label": "Legal",
            "color": "#f3e8ff",
            "accent": "#9333ea",
            "icon": "⚖️",
            "pipelines": {
                "contract_analyzer": {"label": "Contract Analyzer",    "desc": "Parties, obligations, risk flags, missing clauses, key dates"},
                "nda_analyzer":      {"label": "NDA Analyzer",         "desc": "Confidentiality scope, duration, carve-outs, penalties"},
                "court_document":    {"label": "Court Document Parser","desc": "Case number, parties, jurisdiction, hearing dates, deadlines"},
                "due_diligence":     {"label": "Due Diligence Review", "desc": "Bulk risk analysis — IP, litigation, regulatory, financial obligations"},
            },
        },
        "logistics": {
            "label": "Logistics & Supply Chain",
            "color": "#fef3c7",
            "accent": "#d97706",
            "icon": "🚚",
            "pipelines": {
                "waybill_parser":      {"label": "Waybill / Airway Bill Parser", "desc": "Shipper, consignee, package details, tracking, volumetric weight"},
                "purchase_order":      {"label": "Purchase Order Extractor",     "desc": "PO number, line items, quantities, prices, delivery dates"},
                "customs_declaration": {"label": "Customs Declaration Parser",   "desc": "HS codes, declared value, duty, country of origin"},
                "packing_list":        {"label": "Packing List Processor",       "desc": "SKU, quantity, weight per item, batch numbers, carton count"},
            },
        },
        "hr": {
            "label": "HR & Education",
            "color": "#fce7f3",
            "accent": "#db2777",
            "icon": "👤",
            "pipelines": {
                "resume_parser":       {"label": "Resume / CV Parser",     "desc": "Skills, experience timeline, education → ATS-ready JSON + score"},
                "certificate_verifier":{"label": "Certificate Verifier",   "desc": "Institution, candidate, date, grade, expiry validation"},
                "transcript_analyzer": {"label": "Transcript Analyzer",    "desc": "Courses, grades, GPA computation, credit hours, honors"},
            },
        },
        "government": {
            "label": "Government & Compliance",
            "color": "#e0f2fe",
            "accent": "#0284c7",
            "icon": "🏛️",
            "pipelines": {
                "tax_form":          {"label": "Tax Form Reader",         "desc": "W2, 1099, ITR field extraction with arithmetic validation"},
                "permit_license":    {"label": "Permit & License Parser", "desc": "Validity dates, conditions, issuing authority, expiry check"},
                "regulatory_filing": {"label": "Regulatory Filing (SEC)", "desc": "10-K/10-Q/8-K key metrics, risk factors, material changes"},
            },
        },
        "general": {
            "label": "General Purpose",
            "color": "#f1f5f9",
            "accent": "#475569",
            "icon": "📄",
            "pipelines": {
                "document_analyzer": {"label": "Smart Document Analyzer",  "desc": "Auto-detect type, extract entities, dates, amounts, key data"},
            },
        },
    }