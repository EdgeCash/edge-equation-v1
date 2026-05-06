/**
 * /downloads → /data
 *
 * The page that used to live here is now the read-first Data
 * Center at /data. Redirect rather than rewrite so old links stay
 * working and the URL bar reflects the new location.
 */

import { redirect } from "next/navigation";

export default function DownloadsRedirect(): never {
  redirect("/data");
}
