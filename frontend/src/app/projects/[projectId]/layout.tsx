import { ProjectProvider } from "@/components/app/project-provider";

// Projects are private and short-lived: never indexed, never previewed.
export const metadata = { robots: { index: false, follow: false } };

export default async function Layout({ children, params }: { children: React.ReactNode; params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectProvider projectId={projectId}>{children}</ProjectProvider>;
}
