export type IssueReportStatus = "new" | "exported" | "closed";

export type IssueReportCategory =
  | "missing_tool"
  | "error"
  | "documentation"
  | "bug"
  | "other";

export interface IssueReport {
  id: number;
  category: IssueReportCategory;
  summary: string;
  tool: string | null;
  steps: string | null;
  project: string | null;
  assistant: string | null;
  reporters: string[];
  occurrences: number;
  status: IssueReportStatus;
  created_at: string;
  last_reported_at: string;
}
