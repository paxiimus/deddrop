import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

// React 18 has no default fallback UI for a render-time exception — without
// this, any component throwing anywhere silently unmounts the entire tree.
// What that actually looks like: nothing renders, and the near-black body
// background shows through — indistinguishable from "it's just dark-themed
// and this page has no content" versus "something genuinely crashed". This
// turns that into a readable error instead of a mystery black screen.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Render error caught by ErrorBoundary:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card">
          <h2>Something broke</h2>
          <p className="error">{this.state.error.message}</p>
          <p className="hint">Check the browser console (F12) for the full error and stack trace.</p>
          <button onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      );
    }
    return this.props.children;
  }
}
