import { createFileRoute } from "@tanstack/react-router";
import { Hero } from "@/components/qad/Hero";
import { UploadZone } from "@/components/qad/UploadZone";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "QAD — AI Audio Director for Cinematic Storytelling" },
      { name: "description", content: "An emotionally intelligent AI that watches your film and composes a fully directed cinematic soundtrack." },
      { property: "og:title", content: "QAD — AI Audio Director" },
      { property: "og:description", content: "Where vision finds its sound." },
    ],
  }),
  component: Index,
});

function Index() {
  return (
    <main className="relative bg-[color:var(--void)] text-foreground min-h-screen overflow-x-clip">
      <Hero />
      <UploadZone />
    </main>
  );
}
