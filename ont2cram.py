#!/usr/bin/env python3
import os
import sys
import h5py
import pysam
import tqdm
import array
import argparse
import re
import numpy
import gzip
from functools import partial

META_INFO = {
    'version'     : '0.0.1',
    'license'     : 'Apache License 2.0',
    'author'      : 'EBI',
    'url'         : 'https://github.com/EGA-archive/ont2cram',
    'keywords'    : 'ONT HDF Fast5 CRAM',
    'description' : 'Oxford Nanopore HDF/Fast5 to CRAM conversion tool'
}

FIRST_TAG    	   = "a0"
LAST_TAG     	   = "zZ"
FILENAME_TAG 	   = "X0"
READ_NUM_TAG_SHORT = "X1"
READ_NUM_TAG_LONG  = "X2"

htslib_parray_types = {
'i1': 'b',
'u1': 'B',
'b1': 'b',
'B1': 'B',
'i2': 'h',
'u2': 'H',
'i4': 'i',
'I4': 'I',
'i8': 'i',
'I8': 'I',
'i' : 'i',
'I' : 'I',
'int8'  : 'b',
'uint8' : 'B',
'int16' : 'h',
'uint16': 'H',
'int32' : 'i',
'uint32': 'I',
'int64' : 'i',
'uint64': 'I',
'int'   : 'i',
'uint'  : 'I',
'f'      : 'f',
'float'  : 'f',
'float32': 'f',
'float64': 'f',
'U': 'u',
'S': 'B',
'a': 'b',
}

def get_array_type(t):
	key = t[:1] if t.startswith(('S','U')) else t
	return htslib_parray_types[key]

class Tag:
    DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    BASE = len(DIGITS)
    def __init__(self, start_tag="00"):
        self.current_tag_num = self.tag_to_int(start_tag)

    def tag_to_int(self, tag_name):
        return self.DIGITS.index(tag_name[0])*self.BASE+self.DIGITS.index(tag_name[1])

    def increment(self):
        self.current_tag_num += 1

    def get_name(self):
        return self.int_to_tag(self.current_tag_num)

    def int_to_tag(self, num):
        assert num >= 0
        base = len(self.DIGITS)
        res = ""
        while not res or num > 0:
            num, i = divmod(num, base)
            res = self.DIGITS[i] + res
        return res

global_dict_attributes = {}

def bytes_to_str(val):
	if type(val) in [bytes, numpy.bytes_]:
		return val.decode('ascii')
	else: 
		return val

def get_tag_type(np_typecode):
    if np_typecode.startswith(('u','i')): return 'i'
    if np_typecode.startswith( 'f'     ): return 'f'
    if np_typecode.startswith(('S','U')): return None
    sys.exit("Unknown type:{}".format(np_typecode))

def convert_t(typ):
    if typ.startswith(('=','>','<','|')): return typ[1:]
    return typ

def get_type(value):
    try:
        return value.dtype
    except AttributeError:
        return  numpy.dtype(type(value))

def convert_type(value):
    typ = convert_t( get_type(value).str ) 
    #print(f"typ={typ}, val_type={type(value)}, value={value[:5] if type(value) is list else value}")
    val = value
    if typ[0]=='S': 
    	if type(value) is numpy.ndarray: 
    		val = b''.join(map(bytes,value))
    	val = val.decode('ascii')
    return ( val, typ[:1] if typ.startswith(('U','S')) else typ )

def is_fastq_path(hdf_path):
    return hdf_path.endswith("BaseCalled_template/Fastq")
    
def is_signal_path(hdf_path):
    return "/Raw" in hdf_path and hdf_path.endswith("/Signal") 

def is_events_path(hdf_path):
    return hdf_path.endswith("/Events")

def types_equal(t1,t2):
    if t1.startswith('S') and t2.startswith('S'): return True
    if t1.startswith('U') and t2.startswith('U'): return True
    return t1==t2 

def process_dataset(hdf_path, columns):
    if is_fastq_path(hdf_path): 
        return
    for column in columns:
        col_name = column[0]
        col_type_str = str(column[1][0]) if isinstance(column[1], tuple) else column[1]
        col_type = convert_t(col_type_str)
        full_key = hdf_path+'/'+col_name
        try:
            if not types_equal( global_dict_attributes[full_key][0], col_type ):
                sys.exit("Column '{}' - different types in fast5 files: {} vs {}".format(full_key,global_dict_attributes[full_key][0],col_type) )
        except KeyError:
            global_dict_attributes[full_key] = [col_type, 0]  
        

def remove_read_number(attribute_path):
    if not "/read_" in attribute_path: 
    	if not "/Reads/Read_" in attribute_path:
    		return attribute_path,None,None

    read_num_long = None
    read_num_short= None

    match_long = re.match(r"(^.*\/read_)({?[0-9a-fA-F\-]+}?)(\/.+)*$", attribute_path)
    if match_long:
    	attribute_path = match_long.group(1)+"XXXXX"+(match_long.group(3) or "")
    	read_num_long  = match_long.group(2) 
        
    match_short = re.match(r"(^.*\/Reads\/Read_)(\d+)(\/.+)*$", attribute_path)
    if match_short:
    	attribute_path = match_short.group(1)+"YYY"+ (match_short.group(3) or "")
    	read_num_short  = match_short.group(2) 
    	
    return attribute_path,read_num_long,read_num_short   

def is_empty_hdf_group(hdf_node):
	return len(hdf_node.keys())==0 and len(hdf_node.attrs.items())==0

def construct_dummy_attr(hdf_path):
	return hdf_path+'/dummy_attr'	
   
def pre_process_group_attrs(_, hdf_node):
    global global_dict_attributes
    
    node_path     = hdf_node.name
    node_path,_,_ = remove_read_number( node_path )
    #if "r" in node_path: print("after="+node_path)      
        
    if type(hdf_node) is h5py.Dataset:
        columns = hdf_node.dtype.fields.items() if hdf_node.dtype.fields else [('noname', hdf_node.dtype.str)]
        process_dataset( node_path, columns )
    else:
    	if is_empty_hdf_group(hdf_node):
    		global_dict_attributes[construct_dummy_attr(node_path)] = ['',1]   	
               
    for key, val in hdf_node.attrs.items():
        full_key = node_path+'/'+key
        try:
            pair = global_dict_attributes[full_key]
            if pair[0] == val: pair[1] += 1
            global_dict_attributes[full_key] = pair
        except KeyError:
            global_dict_attributes[full_key] = [ val, 1 ]

def walk_fast5( filename, walk_group_function ):
    with h5py.File(filename,'r') as f:
        walk_group_function("/", f)
        f.visititems( walk_group_function )

def is_shared_value(value, total_fast5_files):
	return value > total_fast5_files//2


def list_files(dir, condition):
    return [os.path.join(r,file) for r,d,f in os.walk(dir) for file in f if condition(file)]

def read_fastq_from_file(fn):
    with (gzip.open(fn) if fn.endswith(".gz") else open(fn)) as f:
        lines = f.read().splitlines()
        if len(lines)%4 > 0: sys.exit("Invalid FASTQ file: '{}'".format(fn))
        for i in range(0,len(lines),4):
            yield lines[i].split()[0][1:], lines[i+1], lines[i+3]

def write_cram(fast5_base_dir, fast5_files, cram_file, skipsignal, fastq_map):
    total_fast5_files = len(fast5_files)
    comments_list = []
    tag = Tag(FIRST_TAG)
    for key,val in global_dict_attributes.items():

        is_column = True if val[1]==0 else False

        value,hdf_type = (None,val[0]) if is_column else convert_type(val[0])

        tag_and_val = "TG:"+tag.get_name()
        tag.increment()
        if tag_and_val.endswith(LAST_TAG): sys.exit("Running out of Tag space : too many atributes in Fast5")

        if not is_column:
        	if is_shared_value(val[1], total_fast5_files) and "read_number" not in key:
        		tag_and_val += " CV:"+repr(value)

        comments_list.append( "{}:'{}':{} {}".format( "COL" if is_column else "ATR", key, hdf_type, tag_and_val) )
        global_dict_attributes[key][1] = tag_and_val

    header = {  'HD': {'VN': '1.0'},
                'SQ': [{'LN': 0, 'SN': '*'}],
                'CO': comments_list 
               }    

    with pysam.AlignmentFile( cram_file, "wc", header=header, format_options=[b"no_ref=1"] ) as outf:
        #print([k for k in global_dict_attributes.keys() if "Raw" in k] )
        for filename in tqdm.tqdm(fast5_files): 
            with h5py.File(filename,'r') as fast5:

                def get_tag_name_cv_type( hdf_full_path ):
                    pair = global_dict_attributes[hdf_full_path]
                    assert( pair[1].startswith("TG:") )
                    tag_name = pair[1][3:5]
                    pos_CV = pair[1].find("CV:")
                    val_CV = None if pos_CV==-1 else pair[1][pos_CV+3:]  
                    return ( tag_name, val_CV, pair[0] )

                def get_column( dset, col_name ):
                    if col_name=="noname": return dset[()]
                    return dset[col_name]
                    
                def process_dataset( cram_seg, hdf_path, dset, columns ):
                    if is_signal_path(hdf_path) and skipsignal: return
                    if is_events_path(hdf_path) and skipsignal: return
                    if is_fastq_path(hdf_path)                : return
                    for column in columns:
                        col_name             = column[0]
                        tag_name,_,hdf_type  = get_tag_name_cv_type(hdf_path+'/'+col_name)

                        col = get_column(dset,col_name)

                        col_values = col.tolist() if type(col) is numpy.ndarray else col
                        #print(f"hdf_path={hdf_path}, col_name={col_name}, col_type={type(col)}, col_values={col_values[:7]}, res-type={type(col_values[0])}, hdf_type={hdf_type}")
                        tag_val = col_values
                        if type(col_values[0]) is bytes:
                        	tag_val = b'\x03'.join(col_values)                                                 
                        
                        if type(tag_val) is numpy.bytes_: tag_val=bytes(tag_val)
                        if type(tag_val) is list: tag_val = array.array( get_array_type(hdf_type), tag_val )
                        cram_seg.set_tag(tag_name, tag_val)                        
                
                def process_attrs( cram_seg, _, group_or_dset ):
                    name = group_or_dset.name

                    nonlocal fastq_path                                        
                    if is_fastq_path(name): fastq_path=name                    
                    
                    name,read_num_long,read_num_short  = remove_read_number( name ) 
                    
                    if read_num_long : cram_seg.set_tag( READ_NUM_TAG_LONG, read_num_long )
                    if read_num_short: cram_seg.set_tag( READ_NUM_TAG_SHORT, read_num_short )

                    if type(group_or_dset) is h5py.Dataset:
                        columns = group_or_dset.dtype.fields.items() if group_or_dset.dtype.fields else [('noname', None)]
                        process_dataset( cram_seg, name, group_or_dset, columns )
                    else:
                    	if is_empty_hdf_group(group_or_dset):
                    		tag_name,_,_ = get_tag_name_cv_type( construct_dummy_attr(name) )
                    		cram_seg.set_tag(tag_name,1)                     
                    
                    for key, val in group_or_dset.attrs.items():
                        value,hdf_type    = convert_type(val)
                        tag_name,val_CV,_ = get_tag_name_cv_type(name+'/'+key)

                        nonlocal read_id 
                        if key=="read_id": read_id = val
                        
                        try:
                            if repr(value)!=val_CV : cram_seg.set_tag( tag_name, value, get_tag_type(hdf_type) )
                        except ValueError:
                            sys.exit("Could not detemine tag type (val={}, hdf_type={})".format(value,hdf_type))
                            
                read_groups = [fast5]
                if next(iter(fast5)).startswith("read_"):
                	read_groups = [ fast5[k] for k in fast5.keys()]

                for read_group in read_groups:
                	a_s = pysam.AlignedSegment()
                	a_s.set_tag( FILENAME_TAG, os.path.relpath(filename,fast5_base_dir) )

                	read_id    = None
                	fastq_path = None

                	process_attrs(a_s, None, read_group) #root group               	
                	read_group.visititems( partial(process_attrs,a_s) )

                	fastq_lines = ["nofastq",None,None,None]
                	if fastq_path: fastq_lines = read_group[fastq_path].value.splitlines()                
                	if fastq_map:
                	    if not read_id:
                	        sys.exit("Could not find read_id attribute in :'{}', group='{}'".format(filename,read_group.name))
                	    fastq_lines[0] = '@'+bytes_to_str(read_id)
                	    fastq_lines[1],fastq_lines[3] = fastq_map[read_id]
                	    
                	a_s.query_name = fastq_lines[0]
                	a_s.query_sequence=fastq_lines[1]
                	a_s.query_qualities = pysam.qualitystring_to_array(fastq_lines[3])
                	a_s.flag = 4
                	a_s.reference_id = -1
                	a_s.reference_start = 0
                	a_s.mapping_quality = 0
                	a_s.cigar = ()
                	a_s.next_reference_id = -1
                	a_s.next_reference_start=0
                	a_s.template_length=0
                	a_s.is_unmapped = True

                	outf.write(a_s)

def load_fastq(dir):
    print("Loading FASTQ from: '{}'".format(os.path.abspath(dir)) )		
    map = {}
    for f in tqdm.tqdm( list_files(dir,lambda f:".fastq" in f) ):
        for read_id,seq,qual in read_fastq_from_file(f):
            #print(f"read_id={read_id}, type={type(read_id)}")
            map[str.encode(read_id)] = (seq,qual)
    return map

def exit_if_not_dir(d):
    if not os.path.isdir(d): sys.exit( "Not dir: %s" % d )

def run(input_dir, fastq_dir, output_file, skip_signal):
    exit_if_not_dir(input_dir)
    if fastq_dir: exit_if_not_dir(fastq_dir)
    
    fast5_files = list_files( input_dir, lambda f:f.endswith('.fast5') )

    if not fast5_files: sys.exit( "No .fast5 files found in dir '%s'" % input_dir )

    fastq_map = load_fastq(fastq_dir) if fastq_dir else None        

    print("Loading Fast5 from: '{}'".format(os.path.abspath(input_dir)))
    for f in tqdm.tqdm(fast5_files): 
        walk_fast5( f, pre_process_group_attrs )

    print("Writing CRAM to: '{}'".format(os.path.abspath(output_file)))
    write_cram( input_dir, fast5_files, output_file, skip_signal, fastq_map )

    global_dict_attributes.clear()
    

def main():
    print("ont2cram (version {})".format(META_INFO['version']))
    parser = argparse.ArgumentParser(description=META_INFO['description'])
    parser.add_argument('-i','--inputdir', help='Input directory containing Fast5 files', required=True)
    parser.add_argument('-o','--outputfile', help='Output CRAM filename', required=True)
    parser.add_argument('-f','--fastqdir', help='Input directory containing FASTQ files', required=False)
    parser.add_argument('-s','--skipsignal', help='Skips the raw signal data', action='store_true')
    args = parser.parse_args()

    run(args.inputdir, args.fastqdir, args.outputfile, args.skipsignal)

if __name__ == "__main__":
    main()
