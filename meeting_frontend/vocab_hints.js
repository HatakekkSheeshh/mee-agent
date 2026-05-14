/**
 * Whisper Vocabulary Hints — VNG Cloud / GreenNode
 * Chọn product topic → tự điền vocab hints vào Whisper prompt
 * Cập nhật: 2026-04-10
 */

const VOCAB_HINTS = {
  "": {
    label: "-- Chọn chủ đề họp --",
    hints: "",
  },
  "general": {
    label: "General / Chung",
    hints: "GreenNode, VNG Cloud, Region, Availability Zone, Data Center, API, REST API, API Key, API Gateway, Webhook, SDK, CLI, IaC, Terraform, Portal, vConsole, Dashboard, Pay-as-you-go, Quota, SLA, Encryption, Authentication, Authorization, Certificate, SSL, TLS, HTTPS, Firewall, Backup, Recovery, Replication, Failover, Failback, Scale Out, Scale In, Health Check",
  },
  "vks": {
    label: "VKS — Kubernetes Service",
    hints: "VKS, Kubernetes, cluster, node group, node, pod, container, image, deployment, replica, control plane, data plane, master node, worker node, kubelet, kube-proxy, kubeconfig, kubectl, Ingress, Service, ClusterIP, NodePort, LoadBalancer, Persistent Volume, PV, PVC, StorageClass, ConfigMap, Secret, Network Policy, HPA, VPA, vCR, Container Registry, repository, image tag, Terraform, Helm, Helm chart, IaC, Autoscale, Spot Instance, Preemptible Node",
  },
  "vserver": {
    label: "vServer — Virtual Server / Compute",
    hints: "vServer, Virtual Machine, VM, instance, flavor, vCPU, RAM, GPU, VRAM, GPU Server, Block Store, Volume, Root Volume, Data Volume, Snapshot, Image, Custom Image, SSH Key, Key Pair, Floating IP, Elastic IP, Security Group, Network ACL, Route Table, Subnet, VPC, Auto Scaling, Auto Scaling Group, Scaling Policy, Scale Out, Scale In, Cooldown Period, Region HAN-01, HAN-02, HCM-02, HCM-03, HCM-04, Availability Zone, Backup, IOPS, QoS",
  },
  "vstorage": {
    label: "vStorage — Object & File Storage",
    hints: "vStorage, Object Storage, File Storage, Storage Gateway, Bucket, Object, Key, Metadata, Upload, Download, DataSync, vBackup, Disaster Recovery Center, DRC, S3-compatible, Access Key, Secret Key, rclone, NFS, SMB, Mount, Unmount, Retention Policy, Versioning, Lifecycle Policy",
  },
  "vdb": {
    label: "VDB — Database as a Service",
    hints: "VDB, RDS, Relational Database Service, MySQL, MariaDB, PostgreSQL, Redis, Memcached, MemoryStore Database Service, MDS, OpenSearch, Instance, Endpoint, Connection String, Read Replica, Vertical Scaling, Storage Expansion, Automated Backup, Backup Retention, Standalone, High Availability, HA, Failover, pgvector, pg_trgm, PostGIS",
  },
  "ai": {
    label: "AI Stack / AI Platform / AI Infrastructure",
    hints: "AI Stack, AI Gateway, AI Platform, GenAI Studio, LLM, Large Language Model, Embedding Model, Inference Endpoint, Model Registry, Model Catalog, RAG, Retrieval Augmented Generation, Vector Database, OpenAI, Google Gemini, DeepSeek, Llama, GPU H100, L40s, A40, RTX 4090, VRAM, Context Length, Auto-scaling, InfiniBand, Guardrails, Caching, Automatic Fallback, Semantic Search, OpenSearch, pgvector, Agentbase, one-click deploy",
  },
  "vcdn": {
    label: "vCDN — Content Delivery Network",
    hints: "vCDN, Web Accelerator, Object Download, Video on Demand, VOD, Livestream, HLS, Edge Server, Origin Server, Cache, Cache TTL, CDN Purge, Cache Hit Ratio, CNAME, SSL/TLS, HTTP/2, PageRule, Bandwidth Report, Compression, Image Optimization, Security Link",
  },
  "vnetwork": {
    label: "vNetwork — Virtual Network",
    hints: "VPC, Virtual Private Cloud, Subnet, CIDR, Route Table, Network Interface, Security Group, Network ACL, VPC Peering, Cross Connect, Cross Region Connection, Dynamic Routing, Global Load Balancer, GLB, GSLB, Application Load Balancer, ALB, Network Load Balancer, NLB, Load Balancing Pool, Health Check, Sticky Session, Round Robin, Least Connection, SSL Termination, Floating IP",
  },
  "vdns": {
    label: "vDNS — Domain Name System",
    hints: "vDNS, DNS, Domain Name System, A Record, AAAA Record, CNAME Record, MX Record, TXT Record, SOA Record, NS Record, DNS Zone, Nameserver, TTL, DNSSEC, DDoS Protection, Subdomain, Name Resolution",
  },
  "vwaf": {
    label: "vWAF — Web Application Firewall",
    hints: "vWAF, Web Application Firewall, DDoS Protection, Security Policy, Rule, IP Address Rule, Pattern Matching, Allowlist, Blocklist, Rate Limiting, Bot Protection, SQL Injection, XSS, Request Filtering, Custom Rule",
  },
  "iam": {
    label: "IAM — Identity & Access Management",
    hints: "IAM, Identity and Access Management, Root User, IAM User, Service Account, User Group, Role, Policy, Permission, Principal, Resource, Action, Effect, Inline Policy, Managed Policy, Trusted Relationship, Assume Role, Access Key, Secret Key, Session Token, MFA, Multi-Factor Authentication, RBAC, Role-Based Access Control, Audit Trail, vConsole, Partner Portal",
  },
  "kms": {
    label: "KMS — Key Management System",
    hints: "KMS, Key Management System, Encryption Key, GreenNode Managed Key, Customer Managed Key, Master Key, Data Key, Create Key, Delete Key, Enable Key, Disable Key, Import Key, Export Key, Key Alias, Key Rotation, Key State",
  },
  "vmonitor": {
    label: "vMonitor Platform",
    hints: "vMonitor, Monitoring, Observability, Metric, Log, Alert, Dashboard, Widget, Chart, Visualization, Alert Rule, Alert Policy, Notification Channel, Threshold, Trigger, Alert State, OK, WARNING, CRITICAL, Log Search, Log Filtering, Log Aggregation, Field Extraction, Regex, Time Series, Label, Webhook, Slack, Microsoft Teams",
  },
  "datasync": {
    label: "DataSync",
    hints: "DataSync, Transfer Job, Task, Source, Destination, Data Migration, Data Transfer, Amazon S3, Google Cloud Storage, S3-Compatible, On-Premise, Filter Include Prefix, Filter Exclude Prefix, Transfer Status, Retry Transfer, Failed File List, Data Validation",
  },
  "backup_dr": {
    label: "Backup Center & Disaster Recovery",
    hints: "Backup Vault, Backup Plan, Backup Policy, Backup Point, Recovery Point, Full Backup, Incremental Backup, RPO, Recovery Point Objective, RTO, Recovery Time Objective, Disaster Recovery Center, DRC, Server Disaster Recovery, SDR, Replication, Replication Group, Failover, Failback, Test Failover, Veeam, Veeam Backup and Replication, Recovery Plan",
  },
  "vcolo": {
    label: "vColocation / vColo",
    hints: "vColocation, vColo, Colocation, Data Center, Rack, Rack Unit, Cage, Cabinet, Rack Space, Power Supply, Power Meter, Electricity Usage, Temperature, Humidity, Environmental Monitoring, Asset Tracking, Connection Circuit, Bandwidth",
  },
  "vcloudstack": {
    label: "vCloudStack — Hybrid Cloud",
    hints: "vCloudStack, Hybrid Cloud, On-Premise, On-Premises Infrastructure, Cloud Extension, Managed Solution, Data Locality, API Compatibility, Cloud Integration",
  },
  "veka": {
    label: "Veka.ai / vCloudCam — Smart Camera",
    hints: "Veka.ai, vCloudCam, Smart Camera, Surveillance, Face Recognition, Anomaly Detection, Intrusion Alert, Live Streaming, Video Recording, Cloud-based, Real-time Alert",
  },
};

/**
 * Populate the topic dropdown and wire up the change handler.
 * Call once after DOM is ready.
 */
function initVocabHints() {
  const container = document.getElementById("meeting-topic-list");
  if (!container) return;

  function updateVocabHints() {
    const checked = Array.from(container.querySelectorAll("input[type=checkbox]:checked")).map(cb => cb.value);
    const vocabEl = document.getElementById("vocab-hints");
    if (!vocabEl) return;
    if (checked.length === 0) { vocabEl.value = ""; return; }
    const seen = new Set();
    const merged = [];
    for (const key of checked) {
      const hints = VOCAB_HINTS[key]?.hints || "";
      for (const term of hints.split(",")) {
        const t = term.trim();
        if (t && !seen.has(t.toLowerCase())) {
          seen.add(t.toLowerCase());
          merged.push(t);
        }
      }
    }
    vocabEl.value = merged.join(", ");
  }

  for (const [key, data] of Object.entries(VOCAB_HINTS)) {
    if (key === "") continue;
    const label = document.createElement("label");
    label.className = "topic-checkbox-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = key;
    cb.addEventListener("change", updateVocabHints);
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + data.label));
    container.appendChild(label);
  }
}
