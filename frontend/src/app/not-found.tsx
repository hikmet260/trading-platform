import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-[#121214] text-zinc-100 p-4">
      <h2 className="text-3xl font-bold mb-2">404 - Page Not Found</h2>
      <p className="text-zinc-400 mb-6 text-sm">
        The page you are looking for does not exist.
      </p>
      <Link
        href="/"
        className="px-4 py-2 bg-cyan-500 hover:bg-cyan-400 rounded text-sm text-zinc-950 font-semibold transition-colors"
      >
        Return to Terminal
      </Link>
    </div>
  );
}
