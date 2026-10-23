import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Root, RootContent } from "mdast";
import type { Plugin } from "unified";
import type { Source } from "./types";
import { Icon } from "./Icons";
const remarkCitations: Plugin<[], Root> = () => (tree) => {
  const walk = (parent: { children: RootContent[] }) => {
    parent.children = parent.children.flatMap((node) => {
      if (node.type === "text") {
        const pieces: RootContent[] = [];
        const matches = [...node.value.matchAll(/\[(\d+)\]/g)];
        let offset = 0;
        matches.forEach((match) => {
          if (match.index! > offset)
            pieces.push({
              type: "text",
              value: node.value.slice(offset, match.index),
            });
          pieces.push({
            type: "link",
            url: `#source-${match[1]}`,
            children: [{ type: "text", value: match[1] }],
          });
          offset = match.index! + match[0].length;
        });
        if (!matches.length) return [node];
        if (offset < node.value.length)
          pieces.push({ type: "text", value: node.value.slice(offset) });
        return pieces;
      }
      if (
        !["link", "image", "code", "inlineCode", "html"].includes(node.type) &&
        "children" in node
      )
        walk(node as unknown as { children: RootContent[] });
      return [node];
    });
  };
  walk(tree);
};
export function Answer({
  text,
  sources,
  onSource,
}: {
  text: string;
  sources: Source[];
  onSource: (source: Source) => void;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitations]}
        skipHtml
        components={{
          a: ({ href, children }) => {
            const match = /^#source-(\d+)$/.exec(href ?? "");
            const source =
              match && sources.find((item) => item.n === Number(match[1]));
            return source ? (
              <button
                className="citation"
                type="button"
                aria-label={`Open source ${source.n}: ${source.filename}`}
                onClick={() => onSource(source)}
              >
                {source.n}
              </button>
            ) : (
              <span>{children}</span>
            );
          },
          img: () => null,
          table: ({ children }) => (
            <div className="table-scroll">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
export function SourceDialog({
  source,
  onClose,
}: {
  source: Source | null;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (source && !dialog.current?.open) dialog.current?.showModal();
    if (!source && dialog.current?.open) dialog.current.close();
  }, [source]);
  return (
    <dialog
      ref={dialog}
      className="source-dialog"
      aria-labelledby="source-title"
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {source && (
        <div className="dialog-inner">
          <div className="dialog-heading">
            <span className="eyebrow">SOURCE {source.n}</span>
            <button
              className="icon-button"
              aria-label="Close source"
              onClick={onClose}
            >
              <Icon name="close" />
            </button>
          </div>
          <h2 id="source-title">{source.filename}</h2>
          <p className="muted">
            {source.location ||
              (source.page ? `Page ${source.page}` : "Bank reference material")}
          </p>
          <div className="source-excerpt">
            <span className="eyebrow">
              {source.text ? "PASSAGE" : "SOURCE EXCERPT"}
            </span>
            {source.text || source.snippet ? (
              <p>{source.text || source.snippet}</p>
            ) : (
              <p className="muted">
                Passage text was not saved with this reply. Ask again to see a
                source excerpt.
              </p>
            )}
          </div>
          {!source.text && source.snippet && (
            <p className="fine-print">
              This is the excerpt provided with the reply, which may be shorter
              than the full passage.
            </p>
          )}
        </div>
      )}
    </dialog>
  );
}
