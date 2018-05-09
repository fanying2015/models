"""

Steps:
1. Pre-compute the dataloader
2. loop variant by variant
  - get all the scores
3. Return a rich dictionary of predictionsn
targets:
  - predictions
  - exons
inputs:
  - dictionary with all the different scores (nicely organized)

# Improvement ideas:
- store the intermediary arrays using hdf5/zarr
"""
import tempfile
import kipoi
import kipoi.postprocessing.variant_effects.snv_predict as sp
from kipoi.postprocessing.variant_effects import Diff, Logit, VcfWriter, ensure_tabixed_vcf
from kipoi.cli.postproc import _get_scoring_fns
import json
# Allow different python modules to be used here
from sklearn.base import BaseEstimator, TransformerMixin
import inspect
import pandas as pd
import numpy as np
from sklearn_pandas import DataFrameMapper
import os
import sys
from kipoi.utils import read_txt
filename = inspect.getframeinfo(inspect.currentframe()).filename
this_path = os.path.dirname(os.path.abspath(filename))
sys.path.append(this_path)

# custom scripts
from gather import gather_vcfs


class ZeroImputer(BaseEstimator, TransformerMixin):

    def fit(self, X, y=None):
        return self

    def transform(self, X, y=None):
        return pd.DataFrame(X).fillna(0).copy()


def preproc(X, features):
    """Pre-processing pipeline
    """
    mapper = DataFrameMapper([
        (features, [ZeroImputer()]),
    ])
    return mapper.fit_transform(X)


def score_variants(model,
                   dl_args,
                   input_vcf,
                   output_vcf,
                   scores=["logit_ref", "logit_alt", "ref", "alt", "logit", "diff"],
                   num_workers=0,
                   batch_size=32,
                   dataloader=None,
                   source='kipoi'):
    """Score variants: annotate the vcf file using
    model predictions for the refernece and alternative alleles

    Args:
      model: model string or a model class instance
      dl_args: dataloader arguments as a dictionary
      input_vcf: input vcf file path
      output_vcf: output vcf file path
      scores: list of score names to compute. See kipoi.postprocessing.variant_effects.utils.scoring_fns
      num_workers: number of paralell workers to use for dataloadeing
      batch_size: batch_size for dataloading
      source: model source name
    """
    # TODO - add the remaining arguments to the API
    #        - seq_length
    # TODO - allow scores to be a mixture of strings and objects as a list or as a dictionary (ala keras api)
    # TODO - add string->class mapping to
    # https://github.com/kipoi/kipoi/blob/master/kipoi/postprocessing/variant_effects/utils/scoring_fns.py#L12
    #        as present in https://github.com/kipoi/kipoi/blob/master/kipoi/cli/postproc.py#L23
    #
    # The global API of Kipoi should be by default as of the `score_variants` - predict_snvs is too complicated
    # TODO - make this function accessible at: kipoi.postproc.score_variants
    #        - this will follow the command-line API
    #           - call this function in kipoi.cli.postproc.cli_score_variants
    if isinstance(model, str):
        if dataloader is None:
            model = kipoi.get_model(model, source=source, with_dataloader=True)
            Dataloader = model.default_dataloader
        else:
            model = kipoi.get_model(model, source=source, with_dataloader=False)
            if isinstance(dataloader, str):
                Dataloader = kipoi.get_dataloader_factory(dataloader, source=source)
            else:
                Dataloader = dataloader
    vcf_path_tbx = ensure_tabixed_vcf(input_vcf)  # TODO - run this within the function
    writer = VcfWriter(model, input_vcf, output_vcf)
    vcf_to_region = None
    # TODO - simplify this API -> use classess instead of json dumping. The user shouldn't
    # need to use functions starting with _
    default_params = {"rc_merging": "absmax"}
    scr_config = [default_params] * len(scores)
    difftypes = _get_scoring_fns(model, scores, [json.dumps(el) for el in scr_config])
    # ----
    sp.predict_snvs(model,
                    Dataloader,
                    vcf_path_tbx,
                    batch_size=batch_size,
                    dataloader_args=dl_args,
                    num_workers=num_workers,
                    vcf_to_region=vcf_to_region,
                    evaluation_function_kwargs={'diff_types': difftypes},
                    sync_pred_writer=writer)


def test():
    vcf_file = "example_files/example.vcf"
    gtf_file = "example_files/hg19.chr22.gtf"
    fasta_file = "example_files/hg19.chr22.fa"
    add_conservation = False
    batch_size = 32
    num_workers = 0
    tmpdir = '/tmp/KipoiSplice/'
    this_path = "."


def ensure_dirs(fname):
    """Ensure that the basepath of the given file path exists.

    Args:
      fname: (full) file path
    """
    required_path = "/".join(fname.split("/")[:-1])
    if not os.path.exists(required_path):
        os.makedirs(required_path)


def load_data(vcf_file, gtf_file, fasta_file,
              add_conservation=False,
              batch_size=32,
              num_workers=0,
              tmpdir='/tmp/KipoiSplice/'):
    """
    Args:
      vcf_file: Path to the input vcf file
      fasta_file: reference genome fasta file
      gtf_file: path to the GTF file required by the models (Ensemble)
      add_conservation: if True, include the conservation scores in the inputs
          NOTE: This requires the `vcf_file` to be annotated using VEP
      batch_size: batch size to use with all the models
      num_workers: number of workers to use for each model
      tmpdir (optional): path to the temporary directory where to store the predictions
    """

    MODELS = ["MaxEntScan/3prime", "MaxEntScan/5prime", "HAL", "labranchor"]
    features = read_txt(os.path.join(this_path, "features.txt"))

    # Could also be generated on the fly from "MODELS"
    with open("model_table_cols.json", "r") as ifh:
        model_output_col_names = json.load(ifh)

    os.makedirs(tmpdir, exist_ok=True)
    tmpdir = tempfile.mkdtemp(dir=tmpdir)

    # Generate a vcf file for each model
    for model in MODELS:
        # One could even parallelize here using joblib for example
        out_vcf_fpath = os.path.join(tmpdir, model + ".vcf")
        ensure_dirs(out_vcf_fpath)
        dataloader_arguments = {"gtf_file": os.path.abspath(gtf_file),
                                "fasta_file": os.path.abspath(fasta_file)}
        if "rbp_eclip" in model:
            dataloader_arguments["use_linecache"] = True
        score_variants(model,
                       dl_args=dataloader_arguments,
                       input_vcf=os.path.abspath(vcf_file),
                       scores=[
                           # "logit_ref", "logit_alt","logit"
                           "ref", "alt", "diff"
                       ],
                       output_vcf=out_vcf_fpath)  # This won't work, either use replace("/", "_") or generate the folders.

    # Gather the predictions from all the vcf files
    df = gather_vcfs(MODELS, tmpdir, num_workers, model_output_col_names)

    # impute zeros, convert the pandas dataframe to the array
    X = preproc(df, features)

    # Format the predictions nicely -> use the columnames stored in the files
    #   - store the predictions separately

    # Sample variant format: "chr22:26864522:C:['A']"
    extract_var_info = np.vectorize(lambda x, pos: x.split(":")[pos])

    var_ids = df["variant_id"].values

    try:
        os.unlink(tmpdir)
    except:
        pass

    return {
        "inputs": X,
        "metadata": {
            "id": var_ids,  # have the variant ID
            "chr": extract_var_info(var_ids, 0),  # get the chromosome
            "pos": extract_var_info(var_ids, 1).astype(np.int),  # get the position
            "ref": extract_var_info(var_ids, 2),  # get the reference allele
            "alt": extract_var_info(var_ids, 3),  # get the alternative allele
        }
    }
