export interface Document {
  id: string;
  path: string;
  title: string | null;
  updated_at: string;
}

export type ThemeSetting = "light" | "dark" | "system";
export type DefaultLanding = "wiki_home" | "recent" | "last_viewed";

export interface UserSettings {
  theme: ThemeSetting;
  timezone: string | null;
  default_landing: DefaultLanding;
  chat_provider: string | null;
  chat_model: string | null;
  notify_comment_email: boolean;
  notify_update_warning_email: boolean;
  work_role: string | null;
}

export type UserSettingsUpdate = Partial<UserSettings>;

export type { Trigger, TriggerKind } from "@/lib/triggers";

export interface Event {
  id: number;
  ts: string;
  kind: string;
  actor: string | null;
  target: string | null;
  payload: Record<string, unknown>;
}

export interface DocumentActivity {
  owner_display: string;
  agent_name: string | null;
  activity: "read" | "wrote";
  description: string | null;
  registered_at: string;
  expires_at: string;
}

export type CommentScope = "inline" | "page";
export type CommentAuthorKind = "user" | "agent";
export type CommentStatus = "open" | "resolved" | "orphaned";

export interface CommentView {
  id: string;
  doc_path: string;
  thread_root_id: string;
  parent_id: string | null;
  scope: CommentScope;
  anchor_sha: string | null;
  start_offset: number | null;
  end_offset: number | null;
  quoted_text: string | null;
  author_kind: CommentAuthorKind;
  author_user_id: string | null;
  author_display: string | null;
  body: string;
  status: CommentStatus;
  resolved_by_user_id: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CommentThreadView {
  root: CommentView;
  replies: CommentView[];
}

/** Source facts shared by every provenance read (backend WriteProvenance). */
export interface WriteProvenance {
  source_document_id: string | null;
  source_type: string | null;
  source_url: string | null;
  source_title: string | null;
  /** Leading slice of the source document's content, captured at ingest. */
  source_snippet: string | null;
}

/** One ingested document credited to a page (the Sources tab list). */
export interface SourceRef extends WriteProvenance {
  last_updated: string;
}

export interface DocumentActivityResponse {
  path: string;
  agents: DocumentActivity[];
}
