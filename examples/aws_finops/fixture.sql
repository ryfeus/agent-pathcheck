-- Synthetic, USD-denominated AWS-style data. No real account or pricing data.
-- Tables and foreign keys are created from the SQLAlchemy Core schema in queries.py.
INSERT INTO costs VALUES
('production','2026-09','EC2','NetAmortizedCost',1200000),
('production','2026-10','EC2','NetAmortizedCost',1650000),
('production','2026-09','RDS','NetAmortizedCost',400000),
('production','2026-10','RDS','NetAmortizedCost',450000),
('production','2026-09','S3','NetAmortizedCost',150000),
('production','2026-10','S3','NetAmortizedCost',180000),
('production','2026-09','Lambda','NetAmortizedCost',50000),
('production','2026-10','Lambda','NetAmortizedCost',40000),
('production','2026-09','EC2-Other','NetAmortizedCost',100000),
('production','2026-10','EC2-Other','NetAmortizedCost',160000),
('production','2026-10','ECS','NetAmortizedCost',40000),
('production','2026-09','CloudFront','NetAmortizedCost',30000),
-- Decoys make scope and accounting basis observable in the tests.
('development','2026-10','EC2','NetAmortizedCost',9000000),
('production','2026-10','EC2','UnblendedCost',1900000);

INSERT INTO instances VALUES
('i-web-a','production','m6i.2xlarge','x86_64'),
('i-web-b','production','m6i.2xlarge','x86_64'),
('i-worker-a','production','c6i.2xlarge','x86_64'),
('i-worker-b','production','c6i.2xlarge','x86_64'),
('i-cache-a','production','r6i.2xlarge','x86_64'),
('i-api-a','production','m6i.xlarge','x86_64'),
('i-api-b','production','m6i.xlarge','x86_64'),
('i-batch-a','production','c6i.xlarge','x86_64'),
('i-search-a','production','r6i.xlarge','x86_64'),
('i-arm-a','production','m6i.xlarge','x86_64');

-- Small synthetic target catalog, not a live AWS catalog or capacity model.
INSERT INTO instance_types VALUES
('m6i.xlarge','x86_64'), ('c6i.xlarge','x86_64'), ('r6i.xlarge','x86_64'),
('m6i.large','x86_64'), ('c6i.large','x86_64'), ('r6i.large','x86_64'),
('m6g.large','arm64');

INSERT INTO recommendations VALUES
('rec-web-a','i-web-a','m6i.xlarge',0.5,12000),
('rec-web-b','i-web-b','m6i.xlarge',0.5,11000),
('rec-worker-a','i-worker-a','c6i.xlarge',0.5,10000),
('rec-worker-b','i-worker-b','c6i.xlarge',0.5,9000),
('rec-cache-a','i-cache-a','r6i.xlarge',0.5,8000),
('rec-api-a','i-api-a','m6i.large',0.5,7000),
('rec-api-b','i-api-b','m6i.large',0.5,6000),
('rec-batch-a','i-batch-a','c6i.large',0.5,5000),
('rec-search-a','i-search-a','r6i.large',2.0,4000),
('rec-arm-a','i-arm-a','m6g.large',0.5,3000);

INSERT INTO observations VALUES
('i-web-a','cpu',25.0,14), ('i-web-a','memory',55.0,14),
('i-web-b','cpu',22.0,14), -- Memory was not collected.
('i-worker-a','cpu',30.0,14), ('i-worker-a','memory',45.0,14),
('i-worker-b','cpu',28.0,14), ('i-worker-b','memory',95.0,14),
('i-cache-a','cpu',18.0,14), ('i-cache-a','memory',40.0,14),
('i-api-a','cpu',20.0,14), ('i-api-a','memory',50.0,14),
('i-api-b','cpu',24.0,14), ('i-api-b','memory',65.0,14),
('i-batch-a','cpu',92.0,14), ('i-batch-a','memory',25.0,14),
('i-search-a','cpu',20.0,14), ('i-search-a','memory',40.0,14),
('i-arm-a','cpu',20.0,14), ('i-arm-a','memory',40.0,14);
