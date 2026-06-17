import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownProps {
  children: string;
}

/**
 * Render markdown (GitHub-flavored) for chat replies. pm-agent and the LLM
 * return markdown — bullet lists, bold, links, tables. react-markdown is
 * XSS-safe by default (it does not render raw HTML), so no sanitizer is needed.
 * Links open in a new tab.
 */
export function Markdown({ children }: MarkdownProps) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
