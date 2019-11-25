**Python 3 or higher is required**

# ont2cram

Oxford Nanopore HDF/Fast5 to CRAM conversion tool

## INSTALLATION:

### Direct installation with pip from Github

~~~
pip3 install git+https://github.com/a-slide/ont2cram
~~~

### Installation with pip from Github

~~~
git clone https://github.com/a-slide/ont2cram
cd ont2cram
python3 setup.py install
~~~

## USAGE:

### converter usage (fast5 > CRAM)

~~~
usage: ont2cram converter [-h] -i INPUT_DIR [-o OUTPUT_FILE] [-f FASTQ_DIR]
                          [-m {ignore,skip,error}] [-s] [-e] [-a] [-v] [-q]
                          [-p]

Fast5 to CRAM conversion utility

optional arguments:
  -h, --help            show this help message and exit
  -i INPUT_DIR, --input_dir INPUT_DIR Input directory containing Fast5 files (required)
  -o OUTPUT_FILE, --output_file OUTPUT_FILE Output CRAM filename (default: out.cram)
  -f FASTQ_DIR, --fastq_dir FASTQ_DIR Input directory containing FASTQ files (default: None)
  -m {ignore,skip,error}, --missing_fastq {ignore,skip,error} Behavior when a read id has no corresponding basecalled fastq (default: error)
  -s, --skip_signal     Skips the raw signal data (default: False)
  -e, --include_events  NON-IMPLEMENTED Include the event table data for basecalled fastq (default: False)
  -a, --include_fastq   NON-IMPLEMENTED Include the fastq data for basecalled fastq (default: False)

Verbosity:
  -v, --verbose         Increase verbosity (default: False)
  -q, --quiet           Reduce verbosity (default: False)
  -p, --progress        Display a progress bar (default: False)

~~~

### reverse converter usage (CRAM > fast5)

~~~
usage: ont2cram reverse_converter [-h] -i INPUT_FILE [-o OUTPUT_DIR]
                                  [-t {fastq,fast5,basecalled_fast5}]
                                  [--n_reads_fast5 N_READS_FAST5]
                                  [--n_reads_fastq N_READS_FASTQ] [-v] [-q]
                                  [-p]

CRAM to Fast5 conversion utility (Reverse converterer allowing to restore
original Fast5 collection from Cram generated by ont2cram)

optional arguments:
  -h, --help            show this help message and exit
  -i INPUT_FILE, --input_file INPUT_FILE Input CRAM filename (required)
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR Output directory for generated Fast5 files (default:'current directory'
  -t {fastq,fast5,basecalled_fast5}, --output_type {fastq,fast5,basecalled_fast5} NON-IMPLEMENTED Dump either fastq, fast5 or basecalled_fast5 (default: fast5)
  --n_reads_fast5 N_READS_FAST5 NON-IMPLEMENTED Number of reads per fast5 files (default: 4000)
  --n_reads_fastq N_READS_FASTQ NON-IMPLEMENTED Number of reads per fast5 files (default: 0)

Verbosity:
  -v, --verbose         Increase verbosity (default: False)
  -q, --quiet           Reduce verbosity (default: False)
  -p, --progress        Display a progress bar (default: False)
~~~

## Implementation details:

There is a mapping table in the header that maps ONT attributes/dataset columns to lowercase SAM aux tags eg:
**ATR:'Analyses/Basecall_1D_000/Configuration/calibration_strand/genome_name':S TG:b5 CV:'Lambda_3.6kb'**

general format is :
~~~
[ATR|COL]:'<hdf_attribute_or_column_pathname>':<original_datatype> TG:<2_letter_lowecase_tag> CV:<constant_value>

ATR - mapping between hdf group/dataset attribute and SAM aux tag
COL - mapping between hdf dataset column and SAM aux tag
~~~
**<original_datatype>** is represented using Numpy datatype character codes ( https://docs.scipy.org/doc/numpy/reference/arrays.dtypes.html#specifying-and-constructing-data-types )  

CV part is optional - currently present only for attributes(skipped for dataset columns) and only if >50% of fast5 files have this value. Thus, in current implementation CV is more like 'common value' than constant ( it can be overwritten on the read level for those reads that have different value ).

Tag names are generated sequentially ( a0,a1....aa.....az,aA...aZ...zZ ). If 'zZ' is reached the program exists with an error.

Exceptions(special tags): X0(stores original filename), X1(stores read number in short format), X2(stores read number in long format)

The following paths are expected in HDF(Fast5) : "{path from root}/BaseCalled_template/Fastq" (if FASTQ is present), "{path from root}/Signal" (raw signal), "{path from root}/Events" (for old Fast5 containing events).

HDF datasets are stored in Cram as separate columns - each column in a separate tag

Optional **"--skipsignal"** flag allows to skip raw signal(and Events dataset which is derived from Signal) and produce _much_ smaller Crams

Optional **"--fastqdir"** arg allows to specify input folder(can be the same as "--inputdir") for FASTQ sequences. If "--fastqdir" is specified each FAST5 file is expected to have corresponding FASTQ file in this dir with identical name but different extension(".fastq")

## Precision loss (the following 64-bit attributes currently converted to 32-bit CRAM tags):
* Raw/median_before   ( H5T_IEEE_F64LE )
* Raw/start_time   ( H5T_STD_U64LE  )
* channel_id/digitisation ( H5T_IEEE_F64LE )
* channel_id/offset   ( H5T_IEEE_F64LE )
* channel_id/range   ( H5T_IEEE_F64LE )
* channel_id/sampling_rate( H5T_IEEE_F64LE )

## Open questions/problems:

* HDF has sveral datatypes for strings : Null-padded/Null-terminated, Variable length/Fixed length.The restored type is not always identical to the source e.g. "37-byte null-terminated ASCII string" vs "36-byte null-padded ASCII string"(the content is identical "00730cca-2ff9-4c03-b071-d219ee0a19b8")
* Does it make sense to store HDF layout info(dataset compression method/chunkig settings/dataset max dimensions)?
* Some string values in HDF attributes have line breaks inside - is it valid for Cram tags or better to remove them?
