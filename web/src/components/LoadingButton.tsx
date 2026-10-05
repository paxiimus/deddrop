import type { ButtonHTMLAttributes } from "react";

import Spinner from "./Spinner";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
  loadingText?: string;
}

// One shared pattern instead of repeating the same
// {loading ? <spinner+text> : children} conditional in every button that
// triggers a network call — of which there turned out to be a lot.
export default function LoadingButton({ loading, loadingText = "Working…", children, disabled, ...rest }: Props) {
  return (
    <button disabled={loading || disabled} {...rest}>
      {loading ? (
        <>
          <Spinner size={14} /> {loadingText}
        </>
      ) : (
        children
      )}
    </button>
  );
}
