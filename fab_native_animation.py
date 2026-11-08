"""Decode original keyframes from public OSG preview animation buffers.

Algorithms were checked against the shipped Sketchfab channel serializer and
its varint/spherical codecs. This reads preview resources only; no source
marketplace package or authentication API is involved.
"""
import math
import numpy as np

TYPES={'Float32Array':'<f4','Float64Array':'<f8','Uint8Array':'u1','Uint16Array':'<u2','Uint32Array':'<u4','Int8Array':'i1','Int16Array':'<i2','Int32Array':'<i4'}

def user_data(obj):
    return {item['Name']:item.get('Value')for item in obj.get('UserDataContainer',{}).get('Values',[])}

def number(data,key,default=None):
    value=float(data[key])if key in data else default
    if value is not None and not math.isfinite(value):raise ValueError('Nonfinite channel metadata')
    return value

def array_registry(obj):
    result={}
    def visit(value):
        if isinstance(value,dict):
            if 'UniqueID'in value and 'Array'in value:result[value['UniqueID']]=value
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(obj)
    return result

def read_array(descriptor,binary,registry):
    if 'Array'not in descriptor:
        descriptor=registry[descriptor['UniqueID']]
    kind,meta=next(iter(descriptor['Array'].items()))
    if kind not in TYPES:raise ValueError('Unsupported preview array type: '+kind)
    rows=int(meta['Size']);width=int(descriptor.get('ItemSize',1));count=rows*width;offset=int(meta.get('Offset',0))
    if rows<0 or rows>2_000_000 or width<1 or width>16 or offset<0:raise ValueError('Invalid bounded preview array')
    dtype=np.dtype(TYPES[kind]);data=binary[meta['File']]if isinstance(binary,dict)else binary
    if meta.get('Encoding') not in (None, '', 'varint'):raise ValueError('Unsupported preview array encoding')
    if meta.get('Encoding')=='varint':
        if dtype.kind not in 'iu':raise ValueError('Varint array must be integer')
        values=[];cursor=offset
        for _ in range(count):
            value=0
            for shift in range(0,35,7):
                if cursor>=len(data):raise ValueError('Truncated preview varint')
                byte=data[cursor];cursor+=1;value|=(byte&127)<<shift
                if not byte&128:break
            else:raise ValueError('Oversized preview varint')
            if dtype.kind=='i':value=(value>>1)^-(value&1)
            limit=np.iinfo(dtype)
            if value<limit.min or value>limit.max:raise ValueError('Preview varint exceeds array range')
            values.append(value)
        result=np.asarray(values,dtype=dtype)
    else:
        end=offset+count*dtype.itemsize
        if end>len(data):raise ValueError('Truncated preview array')
        result=np.frombuffer(data,dtype=dtype,count=count,offset=offset).copy()
    return result,rows,width

def unpack_quaternion(vectors,epsilon,nphi):
    """Three quantized axis/half-angle values become an xyzw delta quaternion."""
    if not 2<=nphi<=100000 or not 0<epsilon<=180:raise ValueError('Invalid spherical quaternion metadata')
    result=np.empty((len(vectors),4),dtype=np.float32)
    threshold=math.cos(0.01745329251*epsilon)
    for i,(polar,azimuth,angle)in enumerate(vectors):
        theta=float(polar)*3.14159265359/(nphi-1)
        sine=math.sin(theta);cosine=math.cos(theta)
        next_theta=theta+1.57079632679/(nphi-1)
        boundary=(threshold-cosine*math.cos(next_theta))/max(1e-5,sine*math.sin(next_theta))
        ring=math.ceil(3.14159265359/max(1e-5,math.acos(min(1,max(-1,boundary)))))
        phi=6.28318530718*float(azimuth)/ring
        half_angle=float(angle)*math.pi/65534
        size=math.sin(half_angle)
        result[i]=(size*sine*math.cos(phi),size*sine*math.sin(phi),size*cosine,math.cos(half_angle))
    return result

def quat_product(a,b):
    x,y,z,w=map(float,a);u,v,s,t=map(float,b)
    return np.asarray((w*u+x*t+y*s-z*v,w*v-x*s+y*t+z*u,w*s+x*v-y*u+z*t,w*t-x*u-y*v-z*s),dtype=np.float32)

def decode_component(descriptor,binary,registry,metadata,width,compressed,packed):
    raw,rows,stored_width=read_array(descriptor,binary,registry)
    values=raw.reshape(stored_width,rows).T.copy()if packed and width!=1 else raw.reshape(rows,stored_width).copy()
    mode=int(number(metadata,'channel_mode',0))
    origin=bool(mode&(16 if width==1 else 4))
    if width==4 and mode&8:
        if stored_width!=3:raise ValueError('Packed spherical quaternion requires three stored components')
        values=unpack_quaternion(values,number(metadata,'epsilon',.25),int(number(metadata,'nphi',720)))
        if origin:
            first=np.asarray([number(metadata,k)for k in ('ox','oy','oz','ow')],dtype=np.float32)
            values=np.vstack((first,values))
            for i in range(1,len(values)):values[i]=quat_product(values[i-1],values[i])
        return values
    if stored_width!=width:raise ValueError('Preview channel width mismatch')
    values=values.astype(np.float32)
    if compressed:
        if width==3 and mode&1:
            lower=np.asarray([number(metadata,k)for k in ('bx','by','bz')],dtype=np.float64)
            scale=np.asarray([number(metadata,k)for k in ('hx','hy','hz')],dtype=np.float64)
            values=(values.astype(np.float64)*scale+lower).astype(np.float32)
        if origin:
            first=np.asarray([number(metadata,k)for k in (('ox','oy','oz')if width==3 else ('ot',))],dtype=np.float32)
            values=np.vstack((first,values))
            for i in range(1,len(values)):values[i]=values[i]+values[i-1]
    return values

def decode_channel(kind,channel,binary,registry):
    basename=kind.removeprefix('osgAnimation.')
    if basename.startswith('Vec3LerpChannel'):width=3;channel_type='vec3'
    elif basename.startswith('QuatSlerpChannel'):width=4;channel_type='quat'
    elif basename.startswith('FloatLerpChannel'):width=1;channel_type='float'
    else:raise ValueError('Unsupported preview animation channel: '+kind)
    metadata=user_data(channel);compressed='Compressed'in basename;packed='Packed'in basename
    mode=int(number(metadata,'channel_mode',0))
    if compressed and (width==1 or mode & ~(21 if width==3 else 28)):
        raise ValueError('Unsupported compressed channel mode')
    values=decode_component(channel['KeyFrames']['Key'],binary,registry,metadata,width,compressed,packed)
    times=decode_component(channel['KeyFrames']['Time'],binary,registry,metadata,1,compressed,False).ravel()
    if len(times)!=len(values)or not len(times):raise ValueError('Preview key/time count mismatch')
    if not np.isfinite(times).all()or not np.isfinite(values).all():raise ValueError('Nonfinite preview animation values')
    if np.any(np.diff(times)<0):raise ValueError('Preview times are not monotonic')
    return {'target':channel['TargetName'],'path':channel['Name'],'type':channel_type,'times':times,'values':values}

def animations(document):
    result=[]
    def visit(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key=='osgAnimation.Animation':result.append(child)
                visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(document)
    return result

def decode_animations(document,enabled_meta,buffers=None):
    if buffers is None:buffers=enabled_meta;enabled_meta=None
    if isinstance(enabled_meta,dict):enabled_meta=enabled_meta.get('results',[])
    enabled=None if enabled_meta is None else {item['uid']:item for item in enabled_meta if item.get('isEnabled',True)}
    registry=array_registry(document);clips=[]
    for animation in animations(document):
        data=user_data(animation);uid=data.get('AnimationUID')
        if enabled is not None and uid not in enabled:continue
        channels=[]
        for wrapper in animation['Channels']:
            kind,channel=next(iter(wrapper.items()));channels.append(decode_channel(kind,channel,buffers,registry))
        if not channels:raise ValueError('Source animation has no channels')
        first=min(float(channel['times'][0])for channel in channels);last=max(float(channel['times'][-1])for channel in channels)
        clips.append({'uid':uid,'name':enabled[uid].get('name',animation['Name'])if enabled is not None else animation['Name'],'firstKeyTime':first,'lastKeyTime':last,'duration':last-first,'channels':channels})
    if enabled is not None and set(enabled)!={clip['uid'] for clip in clips}:
        raise ValueError('Some enabled source animations were not found')
    for clip in clips:
        for channel in clip['channels']:
            channel['times']=channel['times'].tolist()
            channel['values']=channel['values'].ravel().tolist()
    return clips
