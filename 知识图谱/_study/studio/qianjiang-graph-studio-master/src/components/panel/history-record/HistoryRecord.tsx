import React, { useCallback, useEffect, useRef, useState} from 'react'
import { Button, Form, Tooltip, Space, DatePicker, List, message, Spin, Pagination  } from 'antd'
import './HistoryRecord.scss'
import SvgIcon from '@/components/svg-icon/SvgIcon'
import { SearchOutlined, CloseOutlined } from '@ant-design/icons'
import { queryHistoryRecordApi, deleteHistoryRecordApi } from '@/api/chart'
import moment from 'moment'
import classNames from 'classnames'

const { RangePicker } = DatePicker;

type props = {
  edgeTypeNameList: any,
  visible:boolean,
  data: any,
  showDefaultPanel: (item: any) => void
}

const HistoryRecord: React.FC<props> = ({visible, edgeTypeNameList, data, showDefaultPanel }) => {
  const iconData:any = {
    'general': {name: '查询', dom: <SearchOutlined />},
    'complex': {name: '复杂查询', dom: <SvgIcon iconClass='complicated-search' />},
    'text': {name: '查找文本', dom: <SvgIcon iconClass='search-text' />},
    'statistics': {name: '统计查询', dom: <SvgIcon iconClass='statistics' />},
    'findLoop': {name: '查找回路', dom: <SvgIcon iconClass='search-return' />},
    'findPath': {name: '查找路径', dom: <SvgIcon iconClass='findPath' />},
    'findSub': {name: '连通性分析', dom: <SvgIcon iconClass='connectivity-analysis' />},
    'flowDirection': {name: '关系净流向', dom: <SvgIcon iconClass='relation-trend' />},
    'searchSameTagRelation': { name: '同类节点关系', dom: <SvgIcon iconClass='same-type-relation' /> }
  }
  const [form] = Form.useForm();
  const formRef = useRef<any>(null)
  const [historyData, setHistoryData] = useState([])
  const [time, setTime] = useState({beginTime: '', endTime: ''})
  const [type, setType] = useState('')
  const [loading, setLoading] = useState<boolean>(false)
  const [totalPage, setTotalPage] = useState(0)
  const [pageData, setPageData] = useState({pageNum: 1, pageSize: 50})

  // 查询历史记录
  const getHistoryRecord = useCallback((type: string, time) => {
    setLoading(true)
    const param = {...time, ...pageData}
    queryHistoryRecordApi({type, ...param}).then((res: any) => {
      if (res?.data) {
        const { list, total } = res.data
        setHistoryData(list)
        setTotalPage(total)
      }
    }).finally(() => {
      setLoading(false)
    })
  }, [pageData])


  useEffect(() => {
    if (visible) {
      getHistoryRecord(type, time)
    } else {
      form.setFieldsValue({
        dateTime: ''
      })
      setTime({beginTime: '', endTime: ''})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, visible, getHistoryRecord])

  function onFinish(values: any) {
    // let time1
    // if (values.dateTime?.length) {
    //   console.log(values);

    //   let beginTime = moment(values.dateTime[0]).format('YYYY-MM-DD HH:mm:ss')
    //   let endTime = moment(values.dateTime[1]).format('YYYY-MM-DD HH:mm:ss')
    //   time1 = {beginTime, endTime}
    // } else {
    //   time1 = {beginTime: '', endTime: ''}
    // }
    // if (time1.beginTime === time.beginTime && time1.endTime === time.endTime) {
    //   getHistoryRecord(type, time)
    // } else {
    //   setTime(time1)
    // }
    getHistoryRecord(type, time)
  }
  // 点击图标 切换类型
  const changeType = (iconType: string) => {
    if (iconType === type) {
      setType("")
      return
    }
    form.setFieldsValue({
      dateTime: ''
    })
    setTime({beginTime: '', endTime: ''})
    setType(iconType)
  }
  const onTimeChange = (time: any) => {
    if (time) {
      let time1
      let beginTime = moment(time[0]).format('YYYY-MM-DD HH:mm:ss')
      let endTime = moment(time[1]).format('YYYY-MM-DD HH:mm:ss')
      time1 = {beginTime, endTime}
      setTime(time1)
    } else {
      form.setFieldsValue({
        dateTime: ''
      })
      setTime({beginTime: '', endTime: ''})
    }
  }
  // 显示弹框
  const showModel = (item: any) => {
    showDefaultPanel(item)
  }
  // 删除历史记录
  const deleteHistoryRecord = (id: string) => {
    deleteHistoryRecordApi({id}).then(() => {
      message.success('删除成功')
      getHistoryRecord(type, time)
    })
  }
  // pageNum pageSize变化
  const onChange = (page: any, pageSize: any) => {
    if (pageSize !== pageData.pageSize) {
      setPageData({pageNum: 1, pageSize})
    } else {
      setPageData({pageNum: page, pageSize})
    }
  }
  return (
    <>
      <div className="historyRecord" style={{display:visible?"block":"none"}}>
      <div className='icons'>
          {Object.keys(iconData).map((key: string) =>
            <div className={classNames({'iconsColor':type===key?true:false})} key={key}>
              <Tooltip title={iconData[key].name}>
                <span onClick={() => changeType(key)}>{iconData[key].dom}</span>
              </Tooltip>
            </div>
            )
          }
        </div>
        {
          <Form
            layout="inline"
            ref={formRef}
            form={form}
            onFinish={onFinish}
          >
            <Form.Item
              name="dateTime"
              style={{width: '300px'}}
            >
              <RangePicker
                showTime={{ format: 'HH:mm:ss' }}
                format="YYYY-MM-DD HH:mm:ss"
                onChange={onTimeChange}
              />
            </Form.Item>
            <Form.Item style={{width: '80px',float:'left'}}>
              <Space style={{ margin: '0 8px 0 20px' }}>
                <Button type='primary' htmlType='submit'>查询</Button>
              </Space>
            </Form.Item>
          </Form>
        }
        <Spin spinning={loading}>
          <List
            bordered
            dataSource={historyData}
            renderItem={(item: any) => (
              <List.Item>
                <span className='icon'>{iconData[item.type]?.dom || <SvgIcon iconClass='explore' />}</span>
                <span className='name' onClick={()=> {showModel(item)}}>{item.name}</span>
                <span className='time'>{item.createTime}</span>
                <CloseOutlined onClick={() => {deleteHistoryRecord(item.id)}}/>
              </List.Item>
            )}
          />
        </Spin>
        <Pagination
          total={totalPage}
          showSizeChanger
          showQuickJumper
          showTotal={total => `共 ${total} 条`}
          defaultPageSize={50}
          pageSizeOptions={[50, 100, 500]}
          current={pageData.pageNum}
          onChange={onChange}
        />

      </div>
    </>
  )
}
HistoryRecord.displayName = 'HistoryRecord'
export {HistoryRecord}
