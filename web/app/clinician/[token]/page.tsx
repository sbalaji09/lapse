export default function ClinicianPage({
  params,
}: {
  params: { token: string };
}) {
  return <div>Clinician {params.token}</div>;
}
