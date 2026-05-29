import { redirect } from "next/navigation";

export default function AdminCatchAllPage() {
  redirect("/admin/language-models");
}
