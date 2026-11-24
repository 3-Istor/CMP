"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type ReactNode } from "react";
import { EmptyState, type EmptyStateProps } from "./EmptyState";
import { PAGE_SIZE_OPTIONS, usePageSize } from "./usePageSize";

export interface DataColumn<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** Sorts on `sortValue`, or on `row[key]` when it is a string or a number. */
  sortable?: boolean;
  sortValue?: (row: T) => string | number | null | undefined;
  align?: "left" | "right" | "center";
  /** Applied to the header and every cell of the column. */
  className?: string;
}

/** Paging done by the API (cursor or offset): the table only renders the page it is given. */
export interface DataTableServerPaging {
  /** Zero-based. */
  page: number;
  pageSize: number;
  total?: number;
  /** For cursor APIs without a total. */
  hasMore?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export interface DataTableProps<T> {
  columns: DataColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  loading?: boolean;
  /** Shown in the body when there is no row (or no match for the search). */
  empty?: EmptyStateProps;
  pageSizeOptions?: number[];
  defaultPageSize?: number;
  /** Remembers the chosen page size in this browser (client paging only). */
  storageKey?: string;
  server?: DataTableServerPaging;
  searchable?: boolean;
  searchKeys?: (keyof T)[];
  searchPlaceholder?: string;
  initialSort?: { key: string; direction: "asc" | "desc" };
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  /** Shown above the table, left of the search box. */
  toolbar?: ReactNode;
  /** Accessible name of the table, read by screen readers only. */
  caption?: string;
  className?: string;
}

const ALIGN = { left: "text-left", right: "text-right", center: "text-center" };

function valueOf<T>(row: T, column: DataColumn<T>) {
  if (column.sortValue) return column.sortValue(row);
  const raw = (row as Record<string, unknown>)[column.key];
  return typeof raw === "string" || typeof raw === "number" ? raw : null;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  empty,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  defaultPageSize,
  storageKey,
  server,
  searchable = false,
  searchKeys,
  searchPlaceholder,
  initialSort,
  caption,
  onRowClick,
  rowClassName,
  toolbar,
  className,
}: DataTableProps<T>) {
  const t = useTranslations("data.table");
  const [storedSize, setStoredSize] = usePageSize(
    server ? undefined : storageKey,
    defaultPageSize,
    pageSizeOptions,
  );
  const [clientPage, setClientPage] = useState(0);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState(initialSort ?? null);

  const pageSize = server?.pageSize ?? storedSize;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (server || !needle || !searchKeys?.length) return rows;
    return rows.filter((row) =>
      searchKeys.some((k) =>
        String(row[k] ?? "")
          .toLowerCase()
          .includes(needle),
      ),
    );
  }, [rows, query, searchKeys, server]);

  const sorted = useMemo(() => {
    const column = sort && columns.find((c) => c.key === sort.key);
    if (!column || server) return filtered;
    const sign = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = valueOf(a, column);
      const vb = valueOf(b, column);
      if (va == null) return vb == null ? 0 : 1;
      if (vb == null) return -1;
      return (
        sign *
        (typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb)))
      );
    });
  }, [filtered, sort, columns, server]);

  const pageCount = server
    ? server.total !== undefined
      ? Math.max(1, Math.ceil(server.total / pageSize))
      : undefined
    : Math.max(1, Math.ceil(sorted.length / pageSize));
  const page = server
    ? server.page
    : Math.min(clientPage, (pageCount ?? 1) - 1);
  const visible = server
    ? rows
    : sorted.slice(page * pageSize, (page + 1) * pageSize);
  const hasNext = server
    ? (server.hasMore ?? (pageCount !== undefined && page + 1 < pageCount))
    : page + 1 < (pageCount ?? 1);

  const goTo = (next: number) =>
    server ? server.onPageChange(next) : setClientPage(next);
  const resize = (size: number) => {
    if (server) server.onPageSizeChange(size);
    else {
      setStoredSize(size);
      setClientPage(0);
    }
  };
  const toggleSort = (key: string) =>
    setSort((s) =>
      s?.key === key
        ? { key, direction: s.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );

  const total = server ? server.total : sorted.length;
  const showPaging =
    loading || page > 0 || hasNext || (total ?? 0) > pageSizeOptions[0];

  return (
    <div className={cn("space-y-2", className)}>
      {(searchable || toolbar) && (
        <div className="flex flex-wrap items-center gap-2">
          {toolbar}
          {searchable && (
            <div className="relative ml-auto w-full max-w-xs">
              <Search className="absolute top-2 left-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setClientPage(0);
                }}
                placeholder={searchPlaceholder ?? t("search")}
                aria-label={searchPlaceholder ?? t("search")}
                className="h-8 pl-8"
              />
            </div>
          )}
        </div>
      )}
      <div className="overflow-x-auto" tabIndex={0}>
        <table className="w-full text-sm" aria-busy={loading}>
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            <tr className="border-b text-xs uppercase tracking-wide text-muted-foreground">
              {columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    aria-sort={
                      active
                        ? sort.direction === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                    className={cn(
                      "px-3 py-2 font-medium",
                      ALIGN[c.align ?? "left"],
                      c.className,
                    )}
                  >
                    {c.sortable && !server ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(c.key)}
                        className={cn(
                          "inline-flex items-center gap-1 uppercase hover:text-foreground",
                          active && "text-foreground",
                        )}
                      >
                        {c.header}
                        {active &&
                          (sort.direction === "asc" ? (
                            <ArrowUp className="h-3 w-3" />
                          ) : (
                            <ArrowDown className="h-3 w-3" />
                          ))}
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: pageSize }, (_, i) => (
                <tr key={i} className="border-b border-border/50" aria-hidden>
                  {columns.map((c) => (
                    <td key={c.key} className={cn("px-3 py-2", c.className)}>
                      <div
                        className={cn(
                          "h-4 animate-pulse rounded bg-muted",
                          c.align === "right" ? "ml-auto w-16" : "w-3/4",
                        )}
                      />
                    </td>
                  ))}
                </tr>
              ))
            ) : visible.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  <EmptyState
                    title={query ? t("noMatch") : t("empty")}
                    {...(query ? {} : empty)}
                  />
                </td>
              </tr>
            ) : (
              visible.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick && (() => onRowClick(row))}
                  className={cn(
                    "border-b border-border/50 transition-colors hover:bg-accent/50",
                    onRowClick && "cursor-pointer",
                    rowClassName?.(row),
                  )}
                >
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={cn(
                        "px-3 py-2",
                        ALIGN[c.align ?? "left"],
                        c.align === "right" && "tabular-nums",
                        c.className,
                      )}
                    >
                      {c.cell(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {showPaging && (
        <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-muted-foreground">
          <label className="flex items-center gap-1.5">
            {t("rowsPerPage")}
            <select
              value={pageSize}
              onChange={(e) => resize(Number(e.target.value))}
              className="h-7 rounded-md border bg-background px-1.5 text-foreground"
            >
              {pageSizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <span className="tabular-nums">
            {pageCount !== undefined
              ? t("pageOf", { page: page + 1, count: pageCount })
              : t("page", { page: page + 1 })}
          </span>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => goTo(page - 1)}
              disabled={loading || page === 0}
              aria-label={t("previous")}
              title={t("previous")}
            >
              <ChevronLeft />
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => goTo(page + 1)}
              disabled={loading || !hasNext}
              aria-label={t("next")}
              title={t("next")}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
