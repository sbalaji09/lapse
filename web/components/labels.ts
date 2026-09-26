// Plain words for everything the engine stores as a key. No screen shows a raw key to a person.
import type { Bucket, CaseStatus, Holder, Source } from "@/lib/types";

export const BUCKET_LABEL: Record<Bucket, string> = {
  SAFE: "Safe",
  PROVABLE: "Provable",
  ONE_AWAY: "One fact away",
  NO_PATH: "No path",
};

export const BUCKET_HINT: Record<Bucket, string> = {
  SAFE: "Already clears. Doing nothing is the right call.",
  PROVABLE: "The chart already proves it. A clinician signs.",
  ONE_AWAY: "One missing fact would clear them.",
  NO_PATH: "Nothing within one fact. Needs help reporting hours.",
};

export const STATUS_LABEL: Record<CaseStatus, string> = {
  needs_action: "Needs action",
  waiting_patient: "Waiting on patient",
  waiting_clinician: "Waiting on clinician",
  attestation_ready: "Attestation ready",
  no_action: "No action",
};

export const SOURCE_LABEL: Record<Source, string> = {
  billing_code: "Billing code",
  structured_record: "Member record",
  note_span: "Clinical note",
  patient_reply: "Patient's own words",
  clinician_attestation: "Clinician attestation",
  external_db: "Database lookup",
};

export const FACT_LABEL: Record<string, string> = {
  qualifying_condition: "Qualifying condition",
  significantly_impairs: "Limits daily activity",
  standing_tolerance_minutes: "Can stand for (minutes)",
  limitation_attested: "Clinician attests the limitation",
  hours_per_month: "Hours per month",
  monthly_income: "Monthly income",
  enrolled_half_time_school: "Enrolled half time or more",
  in_sud_treatment: "In substance use treatment",
  snap_tanf_work_compliant: "Meets SNAP/TANF work rules",
  pregnant_or_postpartum: "Pregnant or postpartum",
  ai_an: "American Indian / Alaska Native",
  released_incarceration_days: "Days since release",
  veteran_total_disability: "VA total disability rating",
  former_foster_youth: "Former foster youth",
  county_hardship: "Lives in a hardship county",
  dependent_child_13_or_under: "Cares for a child 13 or under",
  caregiver_disabled_person: "Cares for a disabled person",
};

export const RULE_LABEL: Record<string, string> = {
  hours: "80 hours of work or activity",
  income: "income",
  school: "school enrollment",
  medically_frail: "medical frailty",
  parent_caretaker: "caring for a child or disabled person",
  pregnant_postpartum: "pregnancy or postpartum",
  ai_an: "American Indian / Alaska Native status",
  veteran_disability: "a total VA disability rating",
  snap_tanf: "SNAP work compliance",
  sud_treatment: "substance use treatment",
  recent_release: "recent release",
  foster_youth: "former foster youth status",
  hardship_county: "a county hardship designation",
};

const DB_ACTION: Record<string, string> = {
  student_enrollment: "student enrollment",
  va: "VA records",
  county: "the county hardship list",
  child_welfare: "child welfare records",
};

export const LANGUAGE_LABEL: Record<string, string> = {
  en: "English", es: "Spanish", vi: "Vietnamese", zh: "Chinese", ko: "Korean", hi: "Hindi", de: "German",
  fr: "French", haw: "Hawaiian", tl: "Tagalog", ru: "Russian", ar: "Arabic", ja: "Japanese",
};

export const firstName = (name: string) => name.split(" ")[0];

export function formatValue(v: unknown): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (v === null || v === undefined) return "None on record";
  return String(v);
}

export function ruleList(ids: string[]): string {
  const words = ids.map((r) => RULE_LABEL[r] ?? r);
  return words.length <= 1 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

export interface NextStep {
  key: string;
  holder: Holder;
  database?: string | null;
}

/** The one thing to do, in the words the button and the audit log both use. */
export function nextStepLabel(step: NextStep, name: string, clinician: string): string {
  const first = firstName(name);
  if (step.holder === "database") return `Check ${DB_ACTION[step.database ?? ""] ?? "the database"}`;
  if (step.holder === "clinician") return `Send to ${clinician}`;
  return `Email ${first} the question`;
}

/** Short queue wording: who holds the fact and what we need from them. */
export function nextStepShort(step: NextStep, name: string, clinician: string): string {
  const first = firstName(name);
  if (step.holder === "database") return `Check ${DB_ACTION[step.database ?? ""] ?? "the database"}`;
  if (step.holder === "clinician") return `Ask ${clinician} to sign`;
  if (step.key === "standing_tolerance_minutes") return `Ask ${first} about standing time`;
  if (step.key === "significantly_impairs") return `Ask ${first} about daily limits`;
  return `Ask ${first}`;
}
