export type Source =
  | "billing_code"
  | "structured_record"
  | "note_span"
  | "patient_reply"
  | "clinician_attestation"
  | "external_db";

export type Holder = "database" | "clinician" | "patient";

export type Tri = "true" | "false" | "unknown";

export type Bucket = "SAFE" | "PROVABLE" | "ONE_AWAY" | "NO_PATH";

export type CaseStatus =
  | "needs_action"
  | "waiting_patient"
  | "waiting_clinician"
  | "attestation_ready"
  | "no_action";

export interface Fact {
  id: string;
  patient_id: string;
  key: string;
  value: boolean | number | string | null;
  source: Source;
  source_ref: Record<string, unknown>;
  quote: string | null;
  recorded_at: string;
  rule_pack_version: string;
}

export interface Note {
  id: string;
  patient_id: string;
  date: string;
  author: string;
  text: string;
}

export interface Claim {
  id: string;
  patient_id: string;
  note_id: string;
  category: string;
  condition: string;
  qualifying_category: boolean;
  significantly_impairs: Tri;
  quote: string;
  start: number;
  end: number;
  verified: boolean | null;
  verifier_reason: string | null;
}

export interface Determination {
  patient_id: string;
  channel: string;
  status: string;
  rule_ids: string[];
  fact_ids: string[];
  rule_pack_version: string;
}

export interface MissingFact {
  id: string;
  patient_id: string;
  key: string;
  holder: Holder;
  database: string | null;
  unlocks_rule: string;
  why: string;
  question: Record<string, string>;
  status: string;
}

export interface Case {
  patient_id: string;
  display_name: string;
  age: number;
  language: string;
  email: string;
  phone: string | null;
  clinic_id: string;
  clinician_name: string;
  renewal_date: string;
  bucket: Bucket;
  fragile: boolean;
  status: CaseStatus;
  determination_a: Determination;
  determination_final: Determination;
  claims: Claim[];
  dropped_claims: Claim[];
  facts: Fact[];
  missing: MissingFact[];
  billed_dx_12mo: Record<string, unknown>[];
  events: Record<string, unknown>[];
}
