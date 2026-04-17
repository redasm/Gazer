import React from 'react';
import FallbackBlock from './blocks/FallbackBlock.jsx';

/**
 * Isolates one MessageBlock's render errors from the rest of the
 * message. If a registered component throws (bad `data`, runtime bug),
 * fall back to the plain-text fallback rather than crashing the whole
 * chat transcript.
 */
class BlockErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError() {
        return { hasError: true };
    }

    componentDidCatch(error, info) {
        // eslint-disable-next-line no-console
        console.error('[chat/BlockErrorBoundary]', error, info);
    }

    render() {
        if (this.state.hasError) {
            return (
                <FallbackBlock
                    fallback={this.props.fallback}
                    component={this.props.component}
                />
            );
        }
        return this.props.children;
    }
}

export default BlockErrorBoundary;
