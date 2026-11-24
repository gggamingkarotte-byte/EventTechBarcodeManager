import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, apikey, content-type, x-client-info, x-eventtech-shared-key",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function secureEqual(left: string, right: string): boolean {
  const encoder = new TextEncoder();
  const a = encoder.encode(left);
  const b = encoder.encode(right);
  let difference = a.length ^ b.length;
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    difference |= (a[index] ?? 0) ^ (b[index] ?? 0);
  }
  return difference === 0;
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "POST required" });
  }

  const sharedKey = Deno.env.get("EVENTTECH_SHARED_KEY") ?? "";
  const suppliedKey = request.headers.get("x-eventtech-shared-key") ?? "";
  if (sharedKey.length < 32 || !secureEqual(sharedKey, suppliedKey)) {
    return jsonResponse(403, { error: "Ungültiger Organisationsschlüssel." });
  }

  const contentLength = Number(request.headers.get("content-length") ?? 0);
  if (contentLength > 8 * 1024 * 1024) {
    return jsonResponse(413, { error: "Sync-Anfrage ist zu groß." });
  }

  let body: { action?: string; changes?: unknown };
  try {
    body = await request.json();
  } catch {
    return jsonResponse(400, { error: "Ungültiges JSON." });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    return jsonResponse(500, { error: "Cloud-Funktion ist nicht vollständig konfiguriert." });
  }
  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  if (body.action === "pull") {
    const records: unknown[] = [];
    for (let start = 0; ; start += 1000) {
      const { data, error } = await supabase
        .from("sync_records")
        .select("id,entity_type,data,revision,deleted")
        .order("id")
        .range(start, start + 999);
      if (error) {
        return jsonResponse(502, { error: "Cloud-Daten konnten nicht gelesen werden." });
      }
      records.push(...(data ?? []));
      if (!data || data.length < 1000) break;
      if (records.length > 100000) {
        return jsonResponse(413, { error: "Cloud-Datenbestand überschreitet das Synchronisierungslimit." });
      }
    }
    return jsonResponse(200, { records });
  }

  if (body.action === "push") {
    if (!Array.isArray(body.changes) || body.changes.length > 200) {
      return jsonResponse(400, { error: "Ungültiger oder zu großer Änderungsbatch." });
    }
    const { data, error } = await supabase.rpc("eventtech_apply_sync", {
      p_changes: body.changes,
    });
    if (error) {
      return jsonResponse(400, { error: "Cloud-Änderungen wurden abgelehnt." });
    }
    return jsonResponse(200, data);
  }

  return jsonResponse(400, { error: "Unbekannte Aktion." });
});
