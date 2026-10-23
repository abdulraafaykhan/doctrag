export type IconName = "plus" | "clip" | "send" | "close" | "shield";
const paths: Record<IconName, string> = {
  plus: "M12 5v14M5 12h14",
  clip: "m8 13 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9",
  send: "m4 4 17 8-17 8 3-8-3-8ZM7 12h14",
  close: "m6 6 12 12M18 6 6 18",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6",
};
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  );
}
