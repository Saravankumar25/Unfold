/** User-facing copy for API error codes (`backend/src/unfold/core/errors.py::ErrorCode`). */
import { isApiError } from "./client";

const MESSAGES: Record<string, string> = {
  INVALID_GITHUB_URL: "That doesn't look like a public GitHub repository URL. Use https://github.com/owner/repo.",
  REPOSITORY_NOT_PUBLIC: "This repository is private or doesn't exist. Unfold only works with public repositories.",
  REPOSITORY_UNAVAILABLE: "GitHub couldn't be reached for this repository. Please try again in a moment.",
  ZIP_INVALID: "This file isn't a valid ZIP archive.",
  ZIP_BOMB_DETECTED: "This archive expands to an unsafe size and was rejected.",
  UNSAFE_ARCHIVE: "This archive contains unsafe paths or links and was rejected.",
  INPUT_TOO_LARGE: "This project is larger than Unfold can analyse (ZIP uploads are limited to 50 MB).",
  UNSUPPORTED_ARCHIVE: "Only .zip archives are supported.",
  EMPTY_REPOSITORY: "The project appears to be empty — there is nothing to analyse.",
  PROVIDER_RATE_LIMITED: "Unfold's AI capacity is busy right now. Please try again shortly.",
  PROVIDER_UNAVAILABLE: "The AI provider is temporarily unavailable. Please try again shortly.",
  PROVIDER_NOT_CONFIGURED: "The AI provider is not configured on this server.",
  LLM_BUDGET_EXHAUSTED: "The AI budget for this project is exhausted. Please try again later.",
  GENERATION_FAILED: "Generation failed. Please try again.",
  EXPORT_FAILED: "The PDF export failed. Please try again.",
  STORE_UNAVAILABLE: "A storage service is unavailable. Please try again shortly.",
  QUEUE_UNAVAILABLE: "The analysis queue is unavailable. Please try again shortly.",
  SESSION_REQUIRED: "Your session could not be started. Please reload the page.",
  SESSION_EXPIRED: "Your session has expired. Projects from it are no longer available.",
  ARTIFACT_EXPIRED: "This export has expired. Create a new one.",
  ARTIFACT_NOT_READY: "The export isn't ready yet.",
  NOT_FOUND: "We couldn't find that. It may have expired with your session.",
  PROJECT_NOT_READY: "This project is still being analysed.",
  VALIDATION_ERROR: "Some input was invalid. Please check the form and try again.",
  RATE_LIMITED: "You're going a little fast. Please wait a moment and try again.",
  INTERNAL_ERROR: "Something went wrong on our side. Please try again.",
  NETWORK_ERROR: "Could not reach Unfold. Check your connection and try again.",
};

/** Prefer the curated copy for known codes; fall back to the server message. */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "VALIDATION_ERROR" && error.message) return error.message;
    return MESSAGES[error.code] ?? error.message ?? "Something went wrong.";
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function messageForCode(code: string | null | undefined, fallback?: string | null): string {
  if (code && MESSAGES[code]) return MESSAGES[code];
  return fallback || "Something went wrong.";
}
