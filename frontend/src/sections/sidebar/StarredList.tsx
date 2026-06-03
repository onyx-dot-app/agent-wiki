"use client";

import {
  closestCenter,
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  restrictToParentElement,
  restrictToVerticalAxis,
} from "@dnd-kit/modifiers";
import {
  arrayMove,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button, SidebarTab } from "@onyx-ai/opal/components";
import { SvgDocFile, SvgStarOff } from "@onyx-ai/opal/icons";

import { reorderStarred, unstarDoc } from "@/lib/starred";
import { docLabel } from "./docLabel";

interface StarredListProps {
  paths: string[];
  pathname: string | null;
  onNavigate: () => void;
}

/** The draggable "Starred" rows. Reordering is optimistic — the SWR
 * cache is updated immediately and rolled back if the PUT fails. */
export function StarredList({ paths, pathname, onNavigate }: StarredListProps) {
  // Require a little movement before a drag starts so plain clicks
  // still navigate to the doc.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = paths.indexOf(String(active.id));
    const to = paths.indexOf(String(over.id));
    if (from === -1 || to === -1) return;
    void reorderStarred(arrayMove(paths, from, to));
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={[restrictToVerticalAxis, restrictToParentElement]}
      onDragEnd={handleDragEnd}
    >
      <SortableContext items={paths} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col gap-px">
          {paths.map((path) => (
            <StarredRow
              key={path}
              path={path}
              selected={pathname === `/app/wiki/${path}`}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

function StarredRow({
  path,
  selected,
  onNavigate,
}: {
  path: string;
  selected: boolean;
  onNavigate: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: path });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`group/starred relative ${isDragging ? "z-10 opacity-75" : ""}`}
      {...attributes}
      {...listeners}
    >
      <SidebarTab
        href={`/app/wiki/${path}`}
        selected={selected}
        icon={SvgDocFile}
        nested
        onClick={onNavigate}
        rightChildren={
          <span className="opacity-0 group-hover/starred:opacity-100">
            <Button
              icon={SvgStarOff}
              prominence="tertiary"
              size="sm"
              tooltip="Unstar"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                void unstarDoc(path);
              }}
            />
          </span>
        }
      >
        {docLabel(path)}
      </SidebarTab>
    </div>
  );
}
