import useSWR from "swr";

import { apiFetch } from "@/lib/api";

export interface DocumentTemplate {
  id: string;
  name: string;
  body: string;
  description: string | null;
  system_prompt: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentTemplateSummary {
  id: string;
  name: string;
  description: string | null;
}

export interface CreateTemplateInput {
  name: string;
  body: string;
  description?: string | null;
  system_prompt?: string | null;
}

export type UpdateTemplateInput = CreateTemplateInput;

/** Picker-facing list (no body / system_prompt) for any authed user. */
export async function listTemplateSummaries(): Promise<DocumentTemplateSummary[]> {
  const r = await apiFetch<{ templates: DocumentTemplateSummary[] }>("/templates");
  return r.templates;
}

export function useTemplateSummaries() {
  const { data, error, isLoading, mutate } = useSWR<{
    templates: DocumentTemplateSummary[];
  }>("/templates");
  return {
    templates: data?.templates ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function getTemplate(id: string): Promise<DocumentTemplate> {
  return apiFetch<DocumentTemplate>(`/templates/${id}`);
}

/** Admin CRUD. */
export function useAdminTemplates() {
  const { data, error, isLoading, mutate } = useSWR<{
    templates: DocumentTemplate[];
  }>("/admin/templates");
  return {
    templates: data?.templates ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function createTemplate(input: CreateTemplateInput): Promise<DocumentTemplate> {
  return apiFetch<DocumentTemplate>("/admin/templates", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateTemplate(
  id: string,
  input: UpdateTemplateInput,
): Promise<DocumentTemplate> {
  return apiFetch<DocumentTemplate>(`/admin/templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteTemplate(id: string): Promise<void> {
  return apiFetch<void>(`/admin/templates/${id}`, { method: "DELETE" });
}

/** Drafting state for a wiki page (null when not drafting from a template). */
export interface DocumentDraftState {
  path: string;
  template_id: string;
  template_name: string | null;
  system_prompt: string | null;
  created_at: string;
}

export function getDraftState(path: string): Promise<DocumentDraftState | null> {
  const qs = new URLSearchParams({ path });
  return apiFetch<DocumentDraftState | null>(`/documents/file/draft?${qs}`);
}

/** Record that ``path`` is being drafted from ``templateId`` (or clear
 *  with null). The server upserts a row; chat traffic for the page then
 *  carries the template's system prompt. */
export function setDraftTemplate(
  path: string, templateId: string | null,
): Promise<DocumentDraftState | null> {
  return apiFetch<DocumentDraftState | null>("/documents/file/draft", {
    method: "POST",
    body: JSON.stringify({ path, template_id: templateId }),
  });
}
