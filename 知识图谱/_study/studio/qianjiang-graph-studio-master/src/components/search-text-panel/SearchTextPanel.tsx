import React, { useState, useEffect } from 'react'
import {AutoComplete, Input, message, Select, SelectProps,} from 'antd'
import {findTextApi} from "@/api/chart"
import FindText from "@/views/chart-explore/find-text/FindText"


import './SearchTextPanel.scss'
type props = {
  mergeData: (data: any) => void,
  visible: boolean,
  edgeTypeNameList:any,
  spaceTagList:any,
  historyData: any
}

export const SearchTextPanel: React.FC<props> = ({ mergeData, visible, historyData}) => {
  const [options, setOptions] = useState<SelectProps<object>['options']>()
  const [tagId, setTagId] = useState<string>('all')
  const [findTextData, setFindTextData] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(false)
  const [keyword, setKeyWord] = useState('')

  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData?.type === 'text') {
      const { mode, keyword } = JSON.parse(historyData.param)
      setTagId(mode)
      setKeyWord(keyword)
    }
  }, [historyData])
  // 选择框选择
  const onSelectChange = (v: any, t:any) => {
    setTagId(v)
  }

  const onDropdownVisibleChange = () => {
    setOptions([])
  }
  // 输入input框
  const onChange = (e: any) => {
    setKeyWord(e)
  }

  const searchOn=(e: any) => {
    let prams={
      mode:tagId,
      keyword:e
    }
    setLoading(true)
    findTextApi(prams).then((res:any)=>{
      if (!res.data) {
        message.warn('未查询到扩展数据')
        setFindTextData(res.data)
        return
      }
      const {edgeList, vertexList} = res.data
      if (!edgeList.length && !vertexList.length) {
        message.warn('未查询到扩展数据')
        setFindTextData(res.data)
        return
      }
        setFindTextData(res.data)
    }).finally(() => {
      setLoading(false)
    })
    setGraphStatisticsVisible(true)
  }
  const prefixSelector = (
    <Select style={{width: 100}} className='search-panel-select' value={tagId} onChange={onSelectChange}
            onDropdownVisibleChange={onDropdownVisibleChange}>
      <Select.Option value="all">全部</Select.Option>
      <Select.Option value="vertex">实体</Select.Option>
      <Select.Option value="edge">连接</Select.Option>
    </Select>
  );
// 查找文本
const [graphStatisticsVisible, setGraphStatisticsVisible] = useState(false)
const showGraphStatistics = (flag: boolean) => {
  setGraphStatisticsVisible(flag)
}
  return (
    <>
      <Input.Group size="large" className="searchTextPanelContainer" style={{height: visible ? 'auto' : 0}}>
        {prefixSelector}
        <AutoComplete
          options={options}
          value={keyword}
          onChange={onChange}
          >
          <Input.Search size="large" onSearch={searchOn}   placeholder="请输入关键字"/>
        </AutoComplete>
      </Input.Group>
      
      <FindText 
      typeValue={tagId}  
      mergeData={mergeData} 
      findTextData={findTextData} 
      visible={graphStatisticsVisible} 
      openModal={showGraphStatistics} 
      loading={loading}
      />
    </>
  )
}
