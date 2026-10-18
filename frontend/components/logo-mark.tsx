import Image from "next/image"
import { cn } from "@/lib/utils"

export function LogoMark({ className }: { className?: string }) {
  return (
    <Image
      src="/logo-mark.png"
      alt=""
      aria-hidden
      width={24}
      height={24}
      priority
      className={cn("size-6 shrink-0 rounded-full", className)}
    />
  )
}

/** "HandsOn" in logo green, "Repo" in white — matches the logo wordmark. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-semibold tracking-tight", className)}>
      <span className="text-[#26a641]">HandsOn</span>
      <span className="text-foreground">Repo</span>
    </span>
  )
}
