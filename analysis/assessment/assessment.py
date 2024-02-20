import pandas as pd
import numpy as np
import torch
import decagon_rank_metrics
import argparse
from os import listdir, system
from kge.model import KgeModel
from kge.util.io import load_checkpoint
from sklearn.metrics import roc_auc_score, average_precision_score


# Get user args
parser = argparse.ArgumentParser()
parser.add_argument('model_checkpoint')
parser.add_argument('out_dir')
parser.add_argument('--partial_results')
args = parser.parse_args()

np.random.seed(0)

# Load checkpoint
checkpoint = load_checkpoint(args.model_checkpoint)
model = KgeModel.create_from(checkpoint)
model_name = model.model
if model_name == 'reciprocal_relations_model':
    model_name = model.config.options.get(
        'reciprocal_relations_model'
        )['base_model']['type']

# Load entity and relation keys
libkge_data_dir = checkpoint['config'].get('dataset.name')
relation_ids = pd.read_csv(
    f'{libkge_data_dir}/relation_ids.del',
    sep='\t',
    header=None,
    index_col=0
).to_dict()[1]
entity_ids = pd.read_csv(
    f'{libkge_data_dir}/entity_ids.del',
    sep='\t',
    header=None,
    index_col=0
).to_dict()[1]

# Load holdout data
holdout = pd.read_csv(
    '../../data/processed/polypharmacy/holdout_polypharmacy.tsv',
    header=None, sep='\t'
)

# Convert holdout names to IDs
if all(holdout.dtypes == object):
    entity_name_to_id = {entity_ids[key]: key for key in entity_ids}
    holdout[0] = [entity_name_to_id[name] for name in holdout[0]]
    holdout[2] = [entity_name_to_id[name] for name in holdout[2]]

    relation_name_to_id = {relation_ids[key]: key for key in relation_ids}
    holdout[1] = [relation_name_to_id[name] for name in holdout[1]]
elif any(holdout.dtypes == object):
    raise ValueError(
        'Appears that there is a mix of IDs and strings in holdout data.'
    )

# Create out df
if args.partial_results:
    results = pd.read_csv(args.partial_results)
else:
    results = pd.DataFrame(columns=['Relation', 'AUROC', 'AUPRC', 'AP@50'])

# Calculate metrics per relation type
rel_count = len(holdout[1].unique())
for rel_id, subdf in holdout.groupby(1):

    # Check if already assessed
    relation = relation_ids[rel_id]
    if relation not in results.Relation:

        # Get assessment data
        positive_edges = subdf.to_numpy().tolist()
        false_edge_file = f'{relation}.tsv'
        negative_edges_str = pd.read_csv(
            f'false_edges/{false_edge_file}',
            header=None,
            sep='\t'
        ).to_numpy().tolist()
        negative_edges = [
            [
                entity_name_to_id[edge[0]],
                relation_name_to_id[edge[1]],
                entity_name_to_id[edge[2]]
            ]
            for edge in negative_edges_str
        ]

        edges_to_score = positive_edges + negative_edges
        s = torch.Tensor([edge[0] for edge in edges_to_score])
        p = torch.Tensor([rel_id for edge in edges_to_score])
        o = torch.Tensor([edge[2] for edge in edges_to_score])

        # Get predictions
        if model.model != 'reciprocal_relations_model':
            preds = model.score_spo(s, p, o).tolist()
        else:
            preds_s = model.score_spo(s, p, o, direction='s').tolist()
            preds_o = model.score_spo(s, p, o, direction='o').tolist()
            preds = [np.mean(tup) for tup in zip(preds_s, preds_o)]
        labels = [1 for _ in positive_edges] + [0 for _ in negative_edges]
        assert len(preds) == len(labels)

        # Calculate area-under metrics
        roc = roc_auc_score(labels, preds)
        prc = average_precision_score(labels, preds)

        # Calculate average precision at 50 using Decagon's function
        edges_ranked = pd.DataFrame(zip(preds, edges_to_score))
        edges_ranked.sort_values(0, ascending=False, inplace=True)
        ap50 = decagon_rank_metrics.apk(
            positive_edges,
            edges_ranked[1].values,
            k=50
        )

        # Store metrics for target relation
        results.loc[len(results)] = ([relation, roc, prc, ap50])
        results.to_csv(f'{args.out_dir}/results_temp.csv', index=False)

        # Progress update
        print(f'Assessed {relation}. {len(results)}/{rel_count} now done.')
    else:
        print(f'Result found for relation: {relation}. Skipping..')

results.to_csv(f'{args.out_dir}/results_full.csv', index=False)
system('rm results_temp.csv')
