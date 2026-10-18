"use client";

import { usePathname } from "next/navigation";
import FluidNav from "./FluidNav";
import AgentConsole from "./AgentConsole";

export default function Chrome({ children }: { children: React.ReactNode }) {
  // The Copilot page is the assistant; a second floating composer there would compete.
  const onCopilot = (usePathname() || "").startsWith("/copilot");
  return (
    <>
      <FluidNav />
      <main
        id="main-content"
        tabIndex={-1}
        className="relative z-10 mx-auto w-full max-w-[1500px] px-5 pt-28 pb-40 md:px-8 focus:outline-none"
      >
        {children}
      </main>
      {!onCopilot && <AgentConsole />}
    </>
  );
}
