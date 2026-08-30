import ClientRedirect from "./client-redirect";

export function generateStaticParams() {
  return [{ id: "index" }];
}

export default function SingleCaseDynamicPage() {
  return <ClientRedirect />;
}
