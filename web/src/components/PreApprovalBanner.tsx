// SPEC §4.1: "Pre-approval mode — synthetic data only." on every page until an ethics approval is recorded.
// The text comes from the server; if the status is unknown, the banner shows (fail safe).
export const PRE_APPROVAL_TEXT = "Pre-approval mode — synthetic data only.";

export function PreApprovalBanner({ preapproval, text }: { preapproval: boolean | null; text?: string | null }) {
  if (preapproval === false) {
    return null;
  }
  return (
    <p className="banner" role="alert">
      {text ?? PRE_APPROVAL_TEXT}
    </p>
  );
}
