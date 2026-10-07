import React, { useRef, useState } from "react"
import './ProcessLibraryImage.scss'
import { Input, Row, Col, Popover, Modal, Form, message, Button, Tooltip} from "antd"
import editSvg from '../../../assets/svg/edit.svg'
import deleteSvg from '../../../assets/svg/delete.svg'
import ellipsis from '../../../assets/svg/ellipsis.svg'
import API from '../../../utils/api'
import store from '@/redux/store/index'

const { Search } = Input;

type props = {
  imageList: Array<any>,
  libraryId: string
  updataImageList: (data: any) => void,
  searchImageList: (libraryId: any,keyword: any) => void,
}
export  const ProcessLibraryImage: React.FC<props> =(
  {
    imageList,
    libraryId,
    updataImageList,
    searchImageList
  }) =>
  {
    const [editModalShow, setEditModalShow] = useState(false)
    const [editParam] = useState({name: '', remark: '', id: ''})
    // const [popoverVisible, setPopoverVisible] = useState(true)
    // let [clickedImageId, setClickedImageId] = useState('')
    // 搜索关键字
    const onSearch = (value: any) => {
      searchImageList(libraryId,value)
    }
    // 跳转图谱
    const toImage = (item:any) => {
      const { origin, pathname } = window.location
      const spaceId = store.getState().spaceId
      const url = `${origin}${pathname}?caseId=${spaceId}&id=${item.id}`
      window.open(url, '_blank')
    }
    // 删除
    const deleteImage = (id:string) => {
      API.deleteAssociateImage(id).then((res:any) => {
        message.success('删除成功')
        setEditModalShow(false)
        updataImageList(libraryId)
      })
    }

    // 编辑弹窗
    const showEditModal = (item:any) => {
      editParam.id = item.id
      editParam.name = item.name
      editParam.remark = item.remark
      setEditModalShow(true)
    }
    // 关闭弹窗
    const handleCancel = () => {
      setEditModalShow(false)
    }

    // 表单确认
    const onFinish = (value:any) => {
      editParam.name = value.name
      editParam.remark = value.remark ? value.remark : ''
      API.updateAssociateImage(editParam).then(() => {
        message.success('编辑成功')
        updataImageList(libraryId)
      })
      setEditModalShow(false)
    }

    const processLibraryRef = useRef(null)

    const changeDateFormat = (time:any, format = 'YYYY-MM-DD hh:mm:ss') => {
      if(!time) return '';
      var datetime = new Date(time);
      var o: any = {
          'Y+': datetime.getFullYear(),
          "M+": datetime.getMonth() + 1,
          "D+": datetime.getDate(),
          "h+": datetime.getHours(),
          "m+": datetime.getMinutes(),
          "s+": datetime.getSeconds(),
          "q+": Math.floor((datetime.getMonth() + 3) / 3),
          "S": datetime.getMilliseconds()
      };
      if (/(Y+)/.test(format)) format = format.replace(RegExp.$1, (datetime.getFullYear() + "").substr(4 - RegExp.$1.length));
      for (var k in o)
          if (new RegExp("(" + k + ")").test(format)) {
              format = format.replace(RegExp.$1, (RegExp.$1.length === 1) ? (o[k]) : (("00" + o[k]).substr(("" + o[k]).length)));
          }
      return format;
  }
    // 列表渲染
    const listItems = imageList.map((item:any) =>
      <Col key={item.id}>
        <div className="sing-image">
          <img src={'/api/file' + item.imageUrl} alt="" onClick={() => toImage(item)}/>
          <div className="shadow">
            <Tooltip title={`${item.name}`} mouseEnterDelay={0.3}>
            <div className="text">
              <div className="name" >{item.name}</div>
              <div>{changeDateFormat(item.createTime)}</div>
            </div>
            </Tooltip>
              {<Popover
                placement="right"
                trigger="click"
                content={
                <div style={{fontSize: '16px'}}>
                  <div style={{cursor: 'pointer'}} onClick={() => showEditModal(item)}>
                    <img src={editSvg} alt="explore" style={{width:'16px'}} />
                      编辑
                    </div>
                    <div style={{cursor: 'pointer'}} onClick={() => {deleteImage(item.id)}}>
                    <img src={deleteSvg} alt="explore" style={{width:'16px'}}/>
                      删除
                    </div>
                </div>
                }>
                <img src={ellipsis} alt="explore" style={{width:'30px',marginRight:'5px'}}/>
              </Popover>}
          </div>
        </div>
      </Col>
    )

  return (
    <div className="processLibraryImage-container" ref={processLibraryRef}>
      <div className = "search">
        <Search placeholder="关键字搜索" onSearch={onSearch} style={{ width: 200 }} />
      </div>
      <div className="image-container">
        <Row gutter={32}>
          { listItems }
        </Row>
      </div>
      <Modal
        title="编辑关联图谱"
        visible={editModalShow}
        footer={null}
        maskClosable={false}
        getContainer={processLibraryRef.current}
        onCancel={handleCancel}>
          <Form
            onFinish={ onFinish }
            initialValues = {editParam}
            labelCol={{ span: 5 }}
            wrapperCol={{ span: 17 }}>
              <Form.Item label="关系图谱名称" name="name">
                <Input/>
              </Form.Item>
              <Form.Item label="备注" name ="remark">
                <Input/>
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
                <Button type="primary" htmlType="submit">
                  确认
                </Button>
                <Button onClick= { handleCancel } style={{marginLeft: '20px'}}>
                  取消
                </Button>
              </Form.Item>
            </Form>
        </Modal>
    </div>
  )
}
