import React, { useState, Suspense } from 'react'
import { Layout, Menu } from 'antd'
import '@antv/graphin-icons/dist/index.css'
import './App.scss'
import { HashRouter as Router, Route, Routes, Link } from 'react-router-dom'
import router  from './router'
import Timeline from '@/components/timeline'

const { Header, Content } = Layout


const App: React.FC = () => {
  const pathname = window.location.hash.slice(1) || '/'
  const [defaultSelectedKeys, setDefaultSelectedKeys] =  useState<string[]>([pathname]) // 之前被选中的边类型合集
  const [forceUpdate, setForceUpdate] = useState(true)

  const changePath = (key:string) => {
    setDefaultSelectedKeys([key])
  }
  // 空间id变化，强制刷新图分析页面
  const changeSpaceId = () => {
    setForceUpdate(false)
      setTimeout(() => {
        setForceUpdate(true)
      }, 0);
  }
  return (
    <Router>
      <Routes>
        <Route path='/timeline' element={<Timeline />} />
        <Route path='/*' element={
          <Layout className="layout">
          <Header>
            <div className="logo" />
            <Menu theme="dark" mode="horizontal" defaultSelectedKeys={defaultSelectedKeys}>
              <Menu.Item key="/" onClick={() => {changePath('/')}}>
              <Link to="/"> 图分析 </Link>
              </Menu.Item>
              <Menu.Item key="/schema" onClick={() => {changePath('/schema')}}>
                <Link to="/schema"> Schema </Link>
              </Menu.Item>
            </Menu>
          </Header>
          <Content>
            <Routes>
              {
                router.map((item, i) => {
                  return (
                    <Route key={i} path={item.path} element={
                      <Suspense fallback={<div></div>}>
                        {forceUpdate&& < item.component changeSpaceId={changeSpaceId}/>} 
                      </Suspense>
                    } />
                  )
                })
              }
            </Routes>
          </Content>
        </Layout>
        } />
      </Routes>
      <Routes>
      
      </Routes>
    </Router>

  )
}

export default App
