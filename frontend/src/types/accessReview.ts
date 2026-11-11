export type ReviewDecision = "keep" | "remove" | "change_role";

export type ReviewOutcome =
  | "applying"
  | "unchanged"
  | "applied"
  | "failed"
  | "terraform_task";

export interface AccessReviewItem {
  id: number;
  campaign_id: number;
  scope: "project" | "platform";
  subject: string;
  username: string;
  role: string;
  last_login_at: string | null;
  reviewers: string[];
  decision: ReviewDecision | null;
  new_role: string | null;
  note: string | null;
  decided_by: string | null;
  decided_at: string | null;
  outcome: ReviewOutcome | null;
  outcome_detail: string | null;
  task_done_by: string | null;
  task_done_at: string | null;
}

export interface AccessReviewCampaign {
  id: number;
  status: "open" | "closed";
  trigger: "manual" | "scheduled";
  started_by: string;
  started_at: string;
  due_at: string;
  escalated_at: string | null;
  closed_at: string | null;
  closed_by: string | null;
  total: number;
  decided: number;
  open_tasks: number;
}

export interface InactiveAccount {
  user_id: string;
  username: string;
  last_login_at: string | null;
  tracked_since: string;
  flagged_at: string | null;
  disabled_at: string | null;
  disable_due_at: string | null;
}

export interface AccessReviewOverview {
  me: string;
  can_manage: boolean;
  settings: {
    auto_start: boolean;
    interval_days: number;
    deadline_days: number;
    inactive_after_days: number;
    inactive_grace_days: number;
    inactive_disable_enabled: boolean;
  };
  campaigns: AccessReviewCampaign[];
  accounts: InactiveAccount[];
}

export interface AccessReviewDetail {
  campaign: AccessReviewCampaign;
  items: AccessReviewItem[];
}

export interface MyAccessReview {
  me: string;
  campaign_id: number | null;
  due_at: string | null;
  items: AccessReviewItem[];
}
