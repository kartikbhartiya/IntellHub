import ClientRedirect from "./client-redirect";

export function generateStaticParams() {
  return [{ id: "index" }];
}

export default function CaseDynamicPage() {
  return <ClientRedirect />;
}
