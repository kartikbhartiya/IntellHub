import SuspectIdClientRedirect from "./client-redirect";

export function generateStaticParams() {
  return [{ id: "index" }];
}

export default function SuspectIdDynamicPage() {
  return <SuspectIdClientRedirect />;
}
