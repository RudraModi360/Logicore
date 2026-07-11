"""Quick DNS check — run this first to see if Supabase hostnames resolve."""
import socket

hostnames = [
    "db.ydvizrwvycdzqtrkpuq.supabase.co",
    "aws-0-ap-northeast-2.pooler.supabase.com",
    "supabase.com",
]

print("=== DNS Resolution Check ===\n")
for h in hostnames:
    try:
        ip = socket.gethostbyname(h)
        print(f"  OK    {h:50s} -> {ip}")
    except socket.gaierror as e:
        print(f"  FAIL  {h:50s} -> {e}")

print()
print("If 'db.ydvizrwvycdzqtrkpuq' FAILS but pooler WORKS:")
print("  → Use the pooler connection URL instead")
print("  → Dashboard → Settings → Database → Connection string → Pooler")
print()
print("If ALL fail:")
print("  → Run: ipconfig /flushdns")
print("  → Or switch DNS to 8.8.8.8 / 1.1.1.1")
print("  → Or disable VPN/proxy temporarily")
