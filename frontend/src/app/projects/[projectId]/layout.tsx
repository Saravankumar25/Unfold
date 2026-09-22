import { ProjectProvider } from "@/components/app/project-provider";

export default async function Layout({ children, params }: { children: React.ReactNode; params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectProvider projectId={projectId}>{children}</ProjectProvider>;
}
