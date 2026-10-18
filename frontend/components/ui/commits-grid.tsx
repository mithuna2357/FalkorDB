"use client"

import * as React from "react"
import { cn } from "@/lib/utils";
import type { CSSProperties } from "react";

export const CommitsGrid = ({
  text,
  className,
  revealed = true,
  animateIn = true,
}: {
  text: string;
  className?: string;
  /** false hides the letters (cells keep data-hl so an intro can fly dots into them) */
  revealed?: boolean;
  /** false shows the letters instantly instead of fading them in */
  animateIn?: boolean;
}) => {
  const cleanString = (str: string): string => {
    const upperStr = str.toUpperCase();

    const withoutAccents = upperStr
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");

    const allowedChars = Object.keys(letterPatterns);
    return withoutAccents
      .split("")
      .filter((char) => allowedChars.includes(char))
      .join("");
  };

  const generateHighlightedCells = (text: string) => {
    const cleanedText = cleanString(text);

    const width = Math.max(cleanedText.length * 6, 6) + 1;

    let currentPosition = 1; // we start at 1 to leave space for the top border
    const highlightedCells: number[] = [];

    cleanedText
      .toUpperCase()
      .split("")
      .forEach((char) => {
        if (letterPatterns[char]) {
          const pattern = letterPatterns[char].map((pos) => {
            const row = Math.floor(pos / 50);
            const col = pos % 50;
            return (row + 1) * width + col + currentPosition;
          });
          highlightedCells.push(...pattern);
        }
        currentPosition += 6;
      });

    return {
      cells: highlightedCells,
      width,
      height: 9, // 7+2 for the top and bottom borders
    };
  };

  const {
    cells: highlightedCells,
    width: gridWidth,
    height: gridHeight,
  } = generateHighlightedCells(text);

  // Letters stay in the two bright greens so they read as text; the background flashes use the dark
  // greens and are rarer, so the animation keeps its life without hiding the word.
  const getRandomColor = () => (Math.random() < 0.8 ? "#48d55d" : "#39d353");
  const getRandomFlashColor = () => (Math.random() < 0.5 ? "#016d32" : "#0d4429");

  const getRandomDelay = () => `${(Math.random() * 0.6).toFixed(1)}s`;
  const getRandomFlash = () => +(Math.random() < 0.12);

  // Random colours/delays are fixed per text so re-renders don't reshuffle them.
  const cells = React.useMemo(
    () =>
      Array.from({ length: gridWidth * gridHeight }).map((_, index) => {
        const isHighlighted = highlightedCells.includes(index);
        return {
          isHighlighted,
          flash: !isHighlighted && !!getRandomFlash(),
          delay: getRandomDelay(),
          color: isHighlighted ? getRandomColor() : getRandomFlashColor(),
        };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text]
  );

  const gridRef = React.useRef<HTMLElement>(null);
  const trail = React.useRef<Map<number, number>>(new Map()); // cell index -> time lit
  const raf = React.useRef<number>(0);
  const TRAIL_MS = 650;

  const paint = React.useCallback(() => {
    const el = gridRef.current;
    if (!el) return;
    const now = performance.now();
    for (const [idx, t] of trail.current) {
      const cell = el.children[idx] as HTMLElement | undefined;
      if (!cell) continue;
      const age = (now - t) / TRAIL_MS;
      if (age >= 1) {
        trail.current.delete(idx);
        cell.style.removeProperty("box-shadow");
        cell.style.removeProperty("background-color");
        continue;
      }
      const a = 1 - age;
      cell.style.backgroundColor = `rgba(72, 213, 93, ${(0.25 + 0.75 * a).toFixed(3)})`;
      cell.style.boxShadow = `0 0 ${(8 * a).toFixed(1)}px rgba(72, 213, 93, ${(0.6 * a).toFixed(3)})`;
    }
    if (trail.current.size) raf.current = requestAnimationFrame(paint);
    else raf.current = 0;
  }, []);

  const onMove = (e: React.PointerEvent<HTMLElement>) => {
    const el = gridRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const padX = parseFloat(cs.paddingLeft);
    const padY = parseFloat(cs.paddingTop);
    const innerW = rect.width - padX - parseFloat(cs.paddingRight);
    const innerH = rect.height - padY - parseFloat(cs.paddingBottom);
    const col = Math.floor(((e.clientX - rect.left - padX) / innerW) * gridWidth);
    const row = Math.floor(((e.clientY - rect.top - padY) / innerH) * gridHeight);
    if (col < 0 || row < 0 || col >= gridWidth || row >= gridHeight) return;
    const idx = row * gridWidth + col;
    const now = performance.now();
    // light the cell under the pointer and a soft halo around it
    for (const [dr, dc] of [[0, 0], [0, 1], [0, -1], [1, 0], [-1, 0]]) {
      const r = row + dr, c = col + dc;
      if (r < 0 || c < 0 || r >= gridHeight || c >= gridWidth) continue;
      const i = r * gridWidth + c;
      trail.current.set(i, i === idx ? now : now - TRAIL_MS * 0.45);
    }
    if (!raf.current) raf.current = requestAnimationFrame(paint);
  };

  React.useEffect(() => () => cancelAnimationFrame(raf.current), []);

  return (
    <section
      ref={gridRef}
      onPointerMove={onMove}
      className={cn(
        "w-full max-w-xl bg-card border grid p-1.5 sm:p-3 gap-0.5 sm:gap-1 rounded-[10px] sm:rounded-[15px]",
        className
      )}
      style={{
        gridTemplateColumns: `repeat(${gridWidth}, minmax(0, 1fr))`,
        gridTemplateRows: `repeat(${gridHeight}, minmax(0, 1fr))`,
      }}
    >
      {cells.map(({ isHighlighted, flash, delay, color }, index) => {
        const shouldFlash = revealed && flash;
        const lit = isHighlighted && revealed;

        return (
          <div
            key={index}
            data-hl={isHighlighted ? "" : undefined}
            className={cn(
              `border h-full w-full aspect-square rounded-[4px] sm:rounded-[3px] transition-[background-color] duration-150`,
              lit && animateIn ? "animate-highlight" : "",
              lit && !animateIn ? "bg-(--highlight)" : "",
              shouldFlash ? "animate-flash" : "",
              !lit && !shouldFlash ? "bg-card" : ""
            )}
            style={
              {
                animationDelay: delay,
                "--highlight": color,
              } as CSSProperties
            }
          />
        );
      })}
    </section>
  );
};

const letterPatterns: { [key: string]: number[] } = {
  A: [
    1, 2, 3, 50, 100, 150, 200, 250, 300, 54, 104, 154, 204, 254, 304, 151, 152,
    153,
  ],
  B: [
    0, 1, 2, 3, 4, 50, 100, 150, 151, 200, 250, 300, 301, 302, 303, 304, 54,
    104, 152, 153, 204, 254, 303,
  ],
  C: [0, 1, 2, 3, 4, 50, 100, 150, 200, 250, 300, 301, 302, 303, 304],
  D: [
    0, 1, 2, 3, 50, 100, 150, 200, 250, 300, 301, 302, 54, 104, 154, 204, 254,
    303,
  ],
  E: [0, 1, 2, 3, 4, 50, 100, 150, 200, 250, 300, 301, 302, 303, 304, 151, 152],
  F: [0, 1, 2, 3, 4, 50, 100, 150, 200, 250, 300, 151, 152, 153],
  G: [
    0, 1, 2, 3, 4, 50, 100, 150, 200, 250, 300, 301, 302, 303, 153, 204, 154,
    304, 254,
  ],
  H: [
    0, 50, 100, 150, 200, 250, 300, 151, 152, 153, 4, 54, 104, 154, 204, 254,
    304,
  ],
  I: [0, 1, 2, 3, 4, 52, 102, 152, 202, 252, 300, 301, 302, 303, 304],
  J: [0, 1, 2, 3, 4, 52, 102, 152, 202, 250, 252, 302, 300, 301],
  K: [0, 4, 50, 100, 150, 200, 250, 300, 151, 152, 103, 54, 203, 254, 304],
  L: [0, 50, 100, 150, 200, 250, 300, 301, 302, 303, 304],
  M: [
    0, 50, 100, 150, 200, 250, 300, 51, 102, 53, 4, 54, 104, 154, 204, 254, 304,
  ],
  N: [
    0, 50, 100, 150, 200, 250, 300, 51, 102, 153, 204, 4, 54, 104, 154, 204,
    254, 304,
  ],
  Ñ: [
    0, 50, 100, 150, 200, 250, 300, 51, 102, 153, 204, 4, 54, 104, 154, 204,
    254, 304,
  ],
  O: [1, 2, 3, 50, 100, 150, 200, 250, 301, 302, 303, 54, 104, 154, 204, 254],
  P: [0, 50, 100, 150, 200, 250, 300, 1, 2, 3, 54, 104, 151, 152, 153],
  Q: [
    1, 2, 3, 50, 100, 150, 200, 250, 301, 302, 54, 104, 154, 204, 202, 253, 304,
  ],
  R: [
    0, 50, 100, 150, 200, 250, 300, 1, 2, 3, 54, 104, 151, 152, 153, 204, 254,
    304,
  ],
  S: [1, 2, 3, 4, 50, 100, 151, 152, 153, 204, 254, 300, 301, 302, 303],
  T: [0, 1, 2, 3, 4, 52, 102, 152, 202, 252, 302],
  U: [0, 50, 100, 150, 200, 250, 301, 302, 303, 4, 54, 104, 154, 204, 254],
  V: [0, 50, 100, 150, 200, 251, 302, 4, 54, 104, 154, 204, 253],
  W: [
    0, 50, 100, 150, 200, 250, 301, 152, 202, 252, 4, 54, 104, 154, 204, 254,
    303,
  ],
  X: [0, 50, 203, 254, 304, 4, 54, 152, 101, 103, 201, 250, 300],
  Y: [0, 50, 101, 152, 202, 252, 302, 4, 54, 103],
  Z: [0, 1, 2, 3, 4, 54, 103, 152, 201, 250, 300, 301, 302, 303, 304],
  "0": [1, 2, 3, 50, 100, 150, 200, 250, 301, 302, 303, 54, 104, 154, 204, 254],
  "1": [1, 52, 102, 152, 202, 252, 302, 0, 2, 300, 301, 302, 303, 304],
  "2": [0, 1, 2, 3, 54, 104, 152, 153, 201, 250, 300, 301, 302, 303, 304],
  "3": [0, 1, 2, 3, 54, 104, 152, 153, 204, 254, 300, 301, 302, 303],
  "4": [0, 50, 100, 150, 4, 54, 104, 151, 152, 153, 154, 204, 254, 304],
  "5": [0, 1, 2, 3, 4, 50, 100, 151, 152, 153, 204, 254, 300, 301, 302, 303],
  "6": [
    1, 2, 3, 50, 100, 150, 151, 152, 153, 200, 250, 301, 302, 204, 254, 303,
  ],
  "7": [0, 1, 2, 3, 4, 54, 103, 152, 201, 250, 300],
  "8": [
    1, 2, 3, 50, 100, 151, 152, 153, 200, 250, 301, 302, 303, 54, 104, 204, 254,
  ],
  "9": [1, 2, 3, 50, 100, 151, 152, 153, 154, 204, 254, 304, 54, 104],
  " ": [],
};
