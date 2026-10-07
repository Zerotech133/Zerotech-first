import {useEffect, useState} from 'react'

const useFullScreen = () => {
  const [height, setHeight] = useState('80vh')
  const handleFullScreenChange = () => {
    if (!document.fullscreenElement) {
      setHeight('80vh')
    } else {
      setHeight('100vh')
    }
  }
  useEffect(() => {
    document.addEventListener(
      'fullscreenchange',
      handleFullScreenChange,
      false,
    )
    return () => {
      document.removeEventListener('fullscreenchange', handleFullScreenChange);
    }
  }, [])

  return height
}

export default useFullScreen
