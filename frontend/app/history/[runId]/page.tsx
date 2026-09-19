import { RunReplay } from "@/components/RunReplay";

export default function ReplayPage({ params }: { params: { runId: string } }) {
  return <RunReplay runId={params.runId} />;
}
