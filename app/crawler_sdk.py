"""Level 1 crawler contract: crawl(ctx), explicit optional work, streamed rows."""
from pathlib import Path
import json
from urllib.parse import urlparse
from openpyxl import Workbook,load_workbook
from schema import FIELDS,REQUIRED,field_for
from detail_store import excel_detail

class CrawlContext:
    def __init__(self,input_path,output_path,index,selected,headers,checkpoint,emit,input_item=None,mapping_mode='automatic',image_mode='url',asset_dir=None):
        self.input_path=Path(input_path)
        self.output_dir=Path(output_path).parent
        self.output_path=Path(output_path)
        self.task_id=str(index)
        self.source_row=index+2
        self.selected=frozenset(REQUIRED+selected)
        self.headers=list(headers)
        for field in REQUIRED:
            if not any(field_for(h)==field for h in self.headers):self.headers.append(FIELDS[field])
        self._checkpoint,self._emit=checkpoint,emit
        self._failures=[]
        self.mapping_mode=mapping_mode;self._unmapped=None;self._unmapped_count=0;self._automatic_count=0
        self.image_mode=image_mode;self.image_dir=Path(asset_dir) if asset_dir else self.output_dir/'image_candidates'
        self.detail_image_dir=self.image_dir.parent/'detail-original'
        if input_item is not None:self.item=input_item
        else:
            wb=load_workbook(self.input_path,read_only=True,data_only=True)
            try:
                sheet=wb.active
                heads=[str(v or '') for v in next(sheet.iter_rows(min_row=1,max_row=1,values_only=True))]
                values=next(sheet.iter_rows(min_row=self.source_row,max_row=self.source_row,values_only=True))
                self.item=dict(zip(heads,values))
            finally:wb.close()
        self.product_code=str(self.item.get('품번') or '')
        self._book=Workbook();self._sheet=self._book.active;self._sheet.title='결과'
        self._sheet.append(self.headers)
        self._emit('row',sheet='결과',row=list(self.headers),header=True)
    def enabled(self,field):
        if field not in FIELDS:raise ValueError('알 수 없는 수집 필드: '+field)
        return field in self.selected
    def checkpoint(self):self._checkpoint()
    def stage(self,message):
        self.checkpoint();self._emit('stage',message=str(message))
    def log(self,message,level='INFO'):
        if level not in ('INFO','SUCCESS','WARNING','ERROR'):raise ValueError('알 수 없는 로그 유형')
        self._emit('log',message=str(message),level=level)
    def collect(self,field,loader):
        """loader is called only when enabled; never pass loader() here."""
        if not self.enabled(field):return None
        self.checkpoint()
        return loader()
    def fail_item(self,message):
        """Record a mandatory/incomplete collection failure while preserving rows."""
        self._failures.append(str(message))
        self._emit('item_failure',message=str(message))
        self.log(str(message),'ERROR')
    def finish(self):
        if self.mapping_mode=='manual' and not self._unmapped_count and not (getattr(self,'allow_automatic_results',False) and self._automatic_count):self.fail_item('수동 매핑 후보가 수집되지 않았습니다.')
        if self._failures:
            raise RuntimeError('품번 수집 미완료: '+' / '.join(self._failures))
    def emit_unmapped(self,record,colors,image_url,shared_detail_html=''):
        """One URL per raw row, with a real palette; no guessed color assignment."""
        if self.mapping_mode!='manual':raise ValueError('mapping_mode=manual 브랜드에서만 사용할 수 있습니다.')
        if self.image_mode=='local':raise ValueError('이미지 저장 유형은 ctx.emit_local_image를 사용하세요.')
        if not (self.enabled('main_image') or self.enabled('additional_images')):raise ValueError('수동 매핑에는 이미지 수집이 필요합니다.')
        if not isinstance(image_url,str) or not image_url.startswith(('https://','http://')):raise ValueError('공개 이미지 URL이 필요합니다.')
        from color_palette import normalize_palette
        colors=normalize_palette(colors)
        record=dict(record,color=','.join(c['label'] for c in colors))
        if self.enabled('main_image'):record['main_image']=image_url
        elif self.enabled('additional_images'):record['additional_images']=image_url
        self._unmapped=dict(colors=colors,image_url=image_url,detail_mode='site_shared',shared_detail_html=shared_detail_html if self.enabled('detail_page') else '')
        try:self.emit_result(record);self._unmapped_count+=1
        finally:self._unmapped=None
    def save_image(self,source,crop=None):
        """Save a validated JPEG crop locally; no upload or color guessing."""
        if self.image_mode!='local':raise ValueError('image_mode=local 브랜드에서만 사용할 수 있습니다.')
        if not (self.enabled('main_image') or self.enabled('additional_images')):raise ValueError('미선택 이미지는 저장할 수 없습니다.')
        self.checkpoint()
        from local_images import save_candidate
        path=save_candidate(self.image_dir,source,self.task_id,crop)
        self.checkpoint();return path
    def save_detail_image(self,source,source_metadata=''):
        if self.image_mode!='local' or not self.enabled('detail_page'):raise ValueError('선택한 로컬 상세이미지만 저장할 수 있습니다.')
        self.checkpoint()
        from local_images import save_candidate
        return save_candidate(self.detail_image_dir,source,self.task_id,detail=True,metadata=source_metadata)
    def emit_local_image(self,record,colors,image_path,mapped_color=None,mapping_evidence='',shared_detail_html='',detail_image_path=None):
        if self.image_mode!='local' or self.mapping_mode!='manual':raise ValueError('로컬 이미지는 image_mode=local / mapping_mode=manual로 등록하세요.')
        if not (self.enabled('main_image') or self.enabled('additional_images')):raise ValueError('미선택 이미지는 전송할 수 없습니다.')
        from color_palette import normalize_palette
        from local_images import inside,validate_saved,fs_path
        palette=normalize_palette(colors);path=Path(image_path).resolve();root=self.image_dir.resolve()
        if root not in path.parents or path.suffix.lower()!='.jpg' or not fs_path(path).is_file():raise ValueError('ctx.save_image가 저장한 JPEG 파일을 전달하세요.')
        candidate=inside(root,path.relative_to(root))
        validate_saved(candidate)
        preassigned=None
        if mapped_color is not None:
            normalized=normalize_palette([mapped_color])[0]
            match=next((c for c in palette if (normalized['color_code'] and c['color_code']==normalized['color_code']) or (not normalized['color_code'] and c['color_name']==normalized['color_name'])),None)
            if not match or not isinstance(mapping_evidence,str) or not mapping_evidence.strip():raise ValueError('공식 색상 후보와 일치하는 사전 매핑 및 실제 인식 근거가 필요합니다.')
            preassigned=match['id']
        relative='images/original/'+candidate.relative_to(root).as_posix()
        record=dict(record,color=','.join(c['label'] for c in palette))
        if self.enabled('main_image'):record['main_image']=candidate.name
        elif self.enabled('additional_images'):record['additional_images']=candidate.name
        self._unmapped=dict(colors=palette,image_url='local:'+relative,asset_path=relative,preassigned_color_id=preassigned,mapping_evidence=str(mapping_evidence),detail_mode='site_shared',shared_detail_html=shared_detail_html if self.enabled('detail_page') else '')
        if detail_image_path is not None:
            detail=Path(detail_image_path).resolve();detail_root=self.detail_image_dir.resolve()
            if not self.enabled('detail_page') or detail_root not in detail.parents:raise ValueError('작업의 상세이미지 저장 경로가 아닙니다.')
            validate_saved(detail,detail=True)
            self._unmapped['detail_asset_path']='images/detail-original/'+detail.relative_to(detail_root).as_posix()
            record['detail_page']=detail.name
        try:self.emit_result(record);self._unmapped_count+=1
        finally:self._unmapped=None
    def emit_result(self,record):
        self.checkpoint()
        if not isinstance(record,dict):raise TypeError('결과는 dict여야 합니다.')
        record=dict(record)
        record.setdefault('product_code',self.product_code)
        if not record.get('product_url'):
            record['product_url']=next((v for h,v in record.items() if field_for(h)=='product_url' and v),'')
        for key in REQUIRED:
            if key=='product_name' and getattr(self,'allow_empty_product_name',False):continue
            if not str(record.get(key) or '').strip():
                raise ValueError('필수 결과 누락: '+key)
        link=record['product_url']
        parsed=urlparse(link) if isinstance(link,str) else None
        if not parsed or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('상품 링크는 실제 상품 상세페이지의 공개 HTTP/HTTPS URL이어야 합니다.')
        for key,value in record.items():
            canonical='color' if key in ('color_name','color_code') else key if key in FIELDS else field_for(key)
            if canonical and canonical not in self.selected and value not in ('',None,[]):
                raise ValueError('미선택 항목을 결과에 넣을 수 없습니다: '+key)
            if canonical is None and (not isinstance(key,str) or not key.strip()):raise ValueError('추가 열 이름은 비어 있지 않은 문자열이어야 합니다.')
        additions=[key for key in record if key not in FIELDS and key not in ('color_name','color_code') and field_for(key) is None and key not in self.headers]
        if additions:
            self.headers.extend(additions)
            for index,header in enumerate(self.headers,1):self._sheet.cell(1,index,header)
            self._emit('row',sheet='결과',row=list(self.headers),header=True)
        row=[]
        for header in self.headers:
            key=field_for(header)
            value=record.get(header,record.get('color_name') if header.replace(' ','')=='색상명' else record.get('color_code') if header.replace(' ','')=='색상코드' else record.get(key))
            if isinstance(value,dict):value=json.dumps(value,ensure_ascii=False,default=str)
            elif isinstance(value,(tuple,list)):value='\n'.join(str(v) for v in value)
            row.append(value)
        excel_row=list(row)
        detail_overflow=False
        for index,header in enumerate(self.headers):
            if field_for(header)=='detail_page' and row[index]:
                excel_row[index],overflow=excel_detail(row[index])
                detail_overflow=detail_overflow or overflow
        # Full strings travel separately from openpyxl, which truncates at 32767.
        self._emit('row',sheet='결과',row=row,header=False,**({'unmapped':self._unmapped} if self._unmapped else {}))
        self._sheet.append(excel_row)
        self._book.save(self.output_path)
        if self._unmapped is None:self._automatic_count+=1
        if detail_overflow:self.fail_item('상세 HTML을 정리해도 Excel 셀 한도(32,767자)를 초과하여 등록용 상세 생성 실패')

