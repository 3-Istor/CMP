"use client";

import { AppIconPanel } from "@/components/app-icon/AppIconPanel";
import { AppConfigPanel } from "@/components/projects/AppConfigPanel";
import { useAppContext } from "@/components/projects/AppContext";
import { AppSection } from "@/components/projects/AppFrame";
import { useProjectContext } from "@/components/projects/ProjectContext";
import { SecurityDataPanel } from "@/components/projects/SecurityDataPanel";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * A search box over its children: every match of the typed text is
 * highlighted (CSS Custom Highlight API, styled in globals.css) and the first
 * one scrolled into view; Enter jumps to the next. Matches field labels and
 * text, not values typed inside inputs.
 */
function SearchHighlight({ children }: { children: React.ReactNode }) {
  const [query, setQuery] = useState("");
  const [count, setCount] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const rangesRef = useRef<Range[]>([]);
  const indexRef = useRef(0);

  const scrollTo = (i: number) => {
    const range = rangesRef.current[i];
    range?.startContainer.parentElement?.scrollIntoView({
      block: "center",
      behavior: "smooth",
    });
  };

  useEffect(() => {
    const root = containerRef.current;
    if (!root || typeof CSS === "undefined" || !CSS.highlights) return;
    const needle = query.trim().toLowerCase();

    const apply = () => {
      const ranges: Range[] = [];
      if (needle) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const text = node.textContent?.toLowerCase() ?? "";
          for (
            let i = text.indexOf(needle);
            i !== -1;
            i = text.indexOf(needle, i + needle.length)
          ) {
            const range = new Range();
            range.setStart(node, i);
            range.setEnd(node, i + needle.length);
            ranges.push(range);
          }
        }
      }
      rangesRef.current = ranges;
      setCount(ranges.length);
      CSS.highlights.set("config-search", new Highlight(...ranges));
    };

    apply();
    indexRef.current = 0;
    scrollTo(0);
    // Panels load and switch components after mount: re-match on changes.
    const observer = new MutationObserver(apply);
    observer.observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    return () => {
      observer.disconnect();
      CSS.highlights.delete("config-search");
    };
  }, [query]);

  return (
    <div className="space-y-4">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || count === 0) return;
            indexRef.current = (indexRef.current + 1) % count;
            scrollTo(indexRef.current);
          }}
          placeholder="Rechercher une option…"
          aria-label="Rechercher une option"
          className="pl-8 pr-24"
        />
        {query.trim() && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
            {count} résultat{count > 1 ? "s" : ""}
          </span>
        )}
      </div>
      <div ref={containerRef} className="space-y-6">
        {children}
      </div>
    </div>
  );
}

export default function AppConfigPage() {
  const { appId, deployment } = useAppContext();
  const { can } = useProjectContext();
  const isRunning = deployment.status === "running";

  return (
    <AppSection
      allowed={deployment.provider_type === "kubernetes" && can("app.viewConfig")}
    >
      <AppIconPanel />
      {isRunning ? (
        <SearchHighlight>
          <div id="security-data" className="scroll-mt-24">
            <SecurityDataPanel deploymentId={appId} />
          </div>
          <AppConfigPanel
            deploymentId={appId}
            readOnly={!can("app.editConfig")}
          />
        </SearchHighlight>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground text-center">
              Configuration will be available once the application is{" "}
              <span className="font-medium text-foreground">running</span>.
            </p>
          </CardContent>
        </Card>
      )}
    </AppSection>
  );
}
