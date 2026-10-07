import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Form, Tooltip, Space, DatePicker, Table, Spin, Pagination, message } from 'antd'
import './AnalysisRecord.scss'
import SvgIcon from '@/components/svg-icon/SvgIcon'//, deleteAnalysisRecordApi
import { queryAnalysisRecordApi } from '@/api/chart'
import moment from 'moment'
import store from '@/redux/store/index'
import classNames from 'classnames'

const { RangePicker } = DatePicker;
type props = {
  visible: boolean,
  mergeData: (data: any) => void
}

const AnalysisRecord: React.FC<props> = ({ visible,mergeData }) => {
  const iconData: any = {
    'findLoop': { name: '查找回路', dom: <SvgIcon iconClass='search-return' /> },
    'findPath': { name: '查找路径', dom: <SvgIcon iconClass='findPath' /> },
    'findSub': { name: '连通性分析', dom: <SvgIcon iconClass='connectivity-analysis' /> },
    'flowDirection': { name: '关系净流向', dom: <SvgIcon iconClass='relation-trend' /> },
    'searchSameTagRelation': { name: '同类节点关系', dom: <SvgIcon iconClass='same-type-relation' /> },

  }
  const columns = [
    {
      title: '序号',
      width: 60,
      render: ( text:any,record:any,index:any ) =>index+1
    },
    {
      title: '案件名称',
      dataIndex: "name",
      key: "name"
    },
    {
      title: '功能名称',
      dataIndex: 'typeName',
      key: "typeName",
      width: 116
    },
    {
      title: '运行状态',
      dataIndex: 'status',
      key: "status",
      width: 90,
    },
    {
      title: '运行时间',
      dataIndex: 'createTime',
      key: "createTime",
      width: 106,
    },
    {
      title: '操作',
      dataIndex: 'operation',
      key: "operation",
      width: 64,
      render: (text:any, record:any) =>(
        <div>
          <span onClick={ ()=> handleTaskResult(record) }  style={{color:'#5192f5', cursor: "pointer"}}>查看</span> 
        </div>
      )
    },
  ];

  const [form] = Form.useForm();
  const formRef = useRef<any>(null)
  const [analysisData, setanalysisData] = useState([])
  const [time, setTime] = useState({ beginTime: "", endTime: "" })
  const [type, setType] = useState('')
  const [loading, setLoading] = useState<boolean>(false)
  const [totalPage, setTotalPage] = useState(0)
  const [pageData, setPageData] = useState({ pageNum: 1, pageSize: 50 })

  // 查询历史记录
  const getAnalysisRecord = useCallback((type: string, time) => {
    setLoading(true)
    let param
    if (time.beginTime.length) {
      param = { ...time, ...pageData }
    } else {
      param = { ...pageData }
    }
    queryAnalysisRecordApi({ type, ...param }).then((res: any) => {
      if (res?.data) {
        const { list, total } = res.data
        list.forEach(item => {
          item.status=item.status===0?"运行中":item.status===-1?"失败":"成功"
          item.createTime = moment(item.createTime).format("YYYY-MM-DD HH:mm:ss");
          item.typeName=iconData[item.type].name
          item.key=item.id
        });
        setanalysisData(list)
        setTotalPage(total)
      }
    }).finally(() => {
      setLoading(false)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageData])


  useEffect(() => {
    if (visible) {
      getAnalysisRecord(type, time)
    } else {
      form.setFieldsValue({
        dateTime: ''
      })
      setTime({ beginTime: "", endTime: "" })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, visible, getAnalysisRecord])

  function onFinish(values: any) {
    getAnalysisRecord(type, time)
  }
  // 打开新的页面并渲染结果
  const toImage = (id:any) => {
    const { origin, pathname } = window.location
    const spaceId = store.getState().spaceId
    const url = `${origin}${pathname}?caseId=${spaceId}&analysisRecordId=${id}`
    window.open(url, '_blank')
  }
  // 点击查看详情
  const handleTaskResult=(record)=>{
    if (record.status=== "成功") {
      toImage(record.id)
    }else{
      message.warn(`状态${record.status},无法查看`)
    }
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
    setTime({ beginTime: "", endTime: "" })
    setType(iconType)
  }
  const onTimeChange = (time: any) => {
    if (time) {
      let time1
      let beginTime = moment(time[0]).format('YYYY-MM-DD HH:mm:ss')
      let endTime = moment(time[1]).format('YYYY-MM-DD HH:mm:ss')
      time1 = { beginTime, endTime }
      setTime(time1)
    } else {
      form.setFieldsValue({
        dateTime: ''
      })
      setTime({ beginTime: "", endTime: "" })
    }
  }
  // 删除历史记录
  // const deleteAnalysisRecord = (id: string) => {
  //   deleteAnalysisRecordApi({ id }).then(() => {
  //     message.success('删除成功')
  //     getAnalysisRecord(type, time)
  //   })
  // }

  // pageNum pageSize变化
  const onChange = (page: any, pageSize: any) => {
    if (pageSize !== pageData.pageSize) {
      setPageData({ pageNum: 1, pageSize })
    } else {
      setPageData({ pageNum: page, pageSize })
    }
  }
  return (
    <>
      <div className="analysisRecord" style={{ display: visible ? "block" : "none" }}>
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
              style={{ width: '300px' }}
            >
              <RangePicker
                showTime={{ format: 'HH:mm:ss' }}
                format="YYYY-MM-DD HH:mm:ss"
                onChange={onTimeChange}
              />
            </Form.Item>
            <Form.Item style={{ width: '80px', float: 'left' }}>
              <Space style={{ margin: '0 8px 0 20px' }}>
                <Button type='primary' htmlType='submit'>查询</Button>
              </Space>
            </Form.Item>
          </Form>
        }
        <Spin spinning={ loading }>
          <div style={{margin:'0 10px'}}>
            <Table size={'small'} bordered columns={ columns } dataSource={ analysisData } pagination={ false } scroll={{ y: 550 }} />
          </div>
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
AnalysisRecord.displayName = 'AnalysisRecord'
export { AnalysisRecord }
