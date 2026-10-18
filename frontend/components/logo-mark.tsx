import Image from "next/image"
import { cn } from "@/lib/utils"

/**
 * The logo is a 256px PNG; request it at 128px so it stays crisp on HiDPI screens even when
 * displayed at 24-32px (next/image would otherwise serve a 32px rendition that looks like a favicon).
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <Image
      src="/logo-mark.png"
      alt=""
      aria-hidden
      width={128}
      height={128}
      quality={100}
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
