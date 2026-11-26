//! Private native checkpoint receiver. Persistence is not engine acceptance.
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::{Path, PathBuf}};
use crate::android_update_coordinator::{coordinator_endpoint, random_nonce, update_http_client};
const MAX_BYTES: usize = 1024 * 1024;
const MAX_REPLY: usize = 2 * MAX_BYTES;

fn hex(s: &str, n: usize) -> bool {
    s.len() == n && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn id(s: &str) -> bool {
    (8..=96).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
fn verify(body: &Value, worker: &str, job: &str, request: &str, digest: &str, nonce: &str) -> Result<Vec<u8>, String> {
    if !id(worker) || !id(job) || !hex(request,32) || !hex(digest,64) || !hex(nonce,32)
        || body["version"].as_u64() != Some(1) || body["authenticated"] != true
        || body["source"] != "coordinator" || body["state"] != "checkpoint_delivered"
        || body["worker_id"] != worker || body["job_id"] != job || body["request_id"] != request
        || body["checkpoint_sha256"] != digest || body["nonce"] != nonce || body["resume_available"] != false {
        return Err("Niepotwierdzona tożsamość checkpointu".into());
    }
    let size = body["checkpoint_size"].as_u64().filter(|v| *v > 0 && *v <= MAX_BYTES as u64).ok_or("Nieprawidłowy rozmiar checkpointu")?;
    let encoded = body["checkpoint_base64"].as_str().filter(|s| s.len() <= MAX_BYTES.div_ceil(3)*4).ok_or("Nieprawidłowe bajty checkpointu")?;
    let raw = STANDARD.decode(encoded).map_err(|_| "Nieprawidłowy Base64 checkpointu")?;
    if raw.len() as u64 != size || format!("{:x}", Sha256::digest(&raw)) != digest {
        return Err("Niezgodny SHA-256 lub rozmiar checkpointu".into());
    }
    let cp: Value = serde_json::from_slice(&raw).map_err(|_| "Nieprawidłowy JSON checkpointu")?;
    if cp["schema_version"].as_u64()!=Some(1) || cp["job_id"]!=job || !matches!(cp["resume_mode"].as_str(), Some("resume" | "restart")) || !cp["payload"].is_object() {
        return Err("Niezgodny schemat checkpointu".into());
    }
    Ok(raw)
}

// Canonical HTTPS coordinator identity isolates checkpoints from other Arenas.
fn coordinator_storage_key(base: &str) -> Result<String, String> {
    let endpoint = coordinator_endpoint(base, "resume-checkpoint")?;
    Ok(format!("{:x}", Sha256::digest(endpoint.as_str().as_bytes())))
}

// Caller supplies an app-private directory. Unique temp file, fsync, atomic
// publication without replacing an existing record, fsync directory.
fn persist(root: &Path, server_key: &str, worker: &str, request: &str, digest: &str, raw: &[u8]) -> Result<PathBuf,String> {
    if !hex(server_key,64) || !id(worker) || !hex(request,32) || !hex(digest,64) || raw.is_empty() || raw.len()>MAX_BYTES || format!("{:x}", Sha256::digest(raw))!=digest { return Err("Nieprawidłowy zapis checkpointu".into()); }
    fs::create_dir_all(root).map_err(|_| "Brak katalogu checkpointów")?;
    if !fs::symlink_metadata(root).map_err(|_| "Brak katalogu")?.file_type().is_dir() { return Err("Nieprawidłowy katalog checkpointów".into()); }
    let identity = format!("{server_key}:{worker}:{request}:{digest}");
    let target = root.join(format!("{:x}.json", Sha256::digest(identity.as_bytes())));
    let temp = root.join(format!(".{}.part",random_nonce()?));
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
    let mut file = options.open(&temp).map_err(|_| "Nie można utworzyć checkpointu tymczasowego")?;
    let result = (|| -> Result<(),String> {
        file.write_all(raw).and_then(|_|file.sync_all()).map_err(|_| "Nie zapisano checkpointu")?;
        match fs::hard_link(&temp,&target) {
            Ok(()) => (),
            Err(e) if e.kind()==std::io::ErrorKind::AlreadyExists => {
                let meta=fs::symlink_metadata(&target).map_err(|_| "Brak checkpointu")?;
                if !meta.file_type().is_file() || meta.len()!=raw.len() as u64 || fs::read(&target).map_err(|_| "Nie odczytano checkpointu")? != raw { return Err("Konflikt zapisanego checkpointu".into()); }
            },
            Err(_) => return Err("Nie opublikowano checkpointu".into()),
        }
        Ok(())
    })();
    drop(file);
    let cleanup=fs::remove_file(&temp);
    result?;
    cleanup.map_err(|_| "Nie usunięto pliku tymczasowego")?;
    fs::File::open(root).and_then(|f|f.sync_all()).map_err(|_| "Nie potwierdzono trwałości katalogu")?;
    Ok(target)
}

/// Internal only: no WebView payload or ACK, no execution authority.
pub(crate) async fn receive(base:&str, worker:&str, token:&str, job:&str, request:&str, digest:&str, root:&Path) -> Result<PathBuf,String> {
    if token.is_empty() || !id(worker) || !id(job) || !hex(request,32) || !hex(digest,64) { return Err("Nieprawidłowa tożsamość pobrania".into()); }
    let nonce=random_nonce()?;
    let mut reply=update_http_client()?.post(coordinator_endpoint(base,"resume-checkpoint")?).json(&serde_json::json!({"worker_id":worker,"token":token,"nonce":nonce,"job_id":job,"request_id":request,"checkpoint_sha256":digest})).send().await.map_err(|_| "Przerwany transfer checkpointu")?;
    if !reply.status().is_success() || reply.content_length().is_some_and(|v|v>MAX_REPLY as u64) { return Err("Arena odmówiła dostarczenia checkpointu".into()); }
    let mut bytes=Vec::new();
    while let Some(chunk)=reply.chunk().await.map_err(|_| "Przerwany transfer checkpointu")? {
        if chunk.len()>MAX_REPLY-bytes.len() { return Err("Przekroczono limit odpowiedzi".into()); }
        bytes.extend_from_slice(&chunk);
    }
    let body=serde_json::from_slice(&bytes).map_err(|_| "Niekompletna odpowiedź checkpointu")?;
    let raw=verify(&body,worker,job,request,digest,&nonce)?;
    persist(root,&coordinator_storage_key(base)?,worker,request,digest,&raw)
}

/// Discover an authorized offer using the existing authenticated status route.
/// No identifiers or checkpoint bytes are accepted from WebView callers.
pub(crate) async fn receive_authorized(base: &str, worker: &str, token: &str, root: &Path) -> Result<&'static str, String> {
    if !id(worker) || token.is_empty() { return Err("Brak tożsamości Workera".into()); }
    let nonce = random_nonce()?;
    let mut reply = update_http_client()?.post(coordinator_endpoint(base, "update-resume-status")?)
        .json(&serde_json::json!({"worker_id":worker,"token":token,"nonce":nonce}))
        .send().await.map_err(|_| "Brak odpowiedzi Areny")?;
    if !reply.status().is_success() { return Err("Arena odmówiła odczytu oferty".into()); }
    let mut bytes = Vec::new();
    while let Some(chunk) = reply.chunk().await.map_err(|_| "Przerwany odczyt oferty")? {
        if chunk.len() > 16384 - bytes.len() { return Err("Oferta przekracza limit".into()); }
        bytes.extend_from_slice(&chunk);
    }
    let body: Value = serde_json::from_slice(&bytes).map_err(|_| "Nieprawidłowa oferta")?;
    if body["authenticated"] != true || body["source"] != "coordinator" || body["worker_id"] != worker || body["nonce"] != nonce || body["resume_available"] != false { return Err("Niepotwierdzona oferta".into()); }
    if body["state"] != "awaiting_local_engine" || body.get("delivery_offer").is_none() { return Ok("checkpoint_not_authorized"); }
    let offer = &body["delivery_offer"];
    let job = offer["job_id"].as_str().ok_or("Brak identyfikatora zadania")?;
    let request = offer["request_id"].as_str().ok_or("Brak identyfikatora STOP")?;
    let digest = offer["checkpoint_sha256"].as_str().ok_or("Brak SHA-256")?;
    if offer["version"].as_u64() != Some(1) || body["job_id"] != job || body["checkpoint_sha256"] != digest { return Err("Niezgodna oferta checkpointu".into()); }
    receive(base, worker, token, job, request, digest, root).await?;
    Ok("checkpoint_stored")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn verifies_exact_bytes_and_refuses_replay_or_corruption() {
        let worker="worker_12345678"; let job="job_12345678"; let request="a".repeat(32); let nonce="b".repeat(32);
        let raw=br#"{"schema_version":1,"job_id":"job_12345678","resume_mode":"restart","payload":{}}"#;
        let digest=format!("{:x}",Sha256::digest(raw));
        let body=serde_json::json!({"version":1,"authenticated":true,"source":"coordinator","state":"checkpoint_delivered","worker_id":worker,"job_id":job,"request_id":request,"checkpoint_sha256":digest,"nonce":nonce,"resume_available":false,"checkpoint_size":raw.len(),"checkpoint_base64":STANDARD.encode(raw)});
        assert_eq!(verify(&body,worker,job,&request,&digest,&nonce).unwrap(),raw);
        for (key,value) in [("nonce",serde_json::json!("wrong")),("worker_id",serde_json::json!("other")),("job_id",serde_json::json!("other")),("request_id",serde_json::json!("wrong")),("checkpoint_size",serde_json::json!(1)),("checkpoint_base64",serde_json::json!("e30=")),("resume_available",serde_json::json!(true))] {
            let mut bad=body.clone(); bad[key]=value; assert!(verify(&bad,worker,job,&request,&digest,&nonce).is_err());
        }
        let root=std::env::temp_dir().join(format!("arena-resume-test-{}",random_nonce().unwrap()));
        let key=coordinator_storage_key("https://arena.example").unwrap();
        let other=coordinator_storage_key("https://other.example").unwrap();
        assert_eq!(key,coordinator_storage_key("https://ARENA.example/").unwrap());
        assert_ne!(key,other);
        assert!(coordinator_storage_key("http://arena.example").is_err());
        let path=persist(&root,&key,worker,&request,&digest,raw).unwrap();
        assert_eq!(persist(&root,&key,worker,&request,&digest,raw).unwrap(),path);
        assert_eq!(fs::read(&path).unwrap(),raw);
        let other_path=persist(&root,&other,worker,&request,&digest,raw).unwrap();
        assert_ne!(path,other_path);
        assert_eq!(fs::read(&other_path).unwrap(),raw);
        fs::write(&path,b"corrupt").unwrap();
        assert!(persist(&root,&key,worker,&request,&digest,raw).is_err());
        assert_eq!(fs::read_dir(&root).unwrap().count(),2);
        fs::remove_dir_all(root).unwrap();
    }
}
